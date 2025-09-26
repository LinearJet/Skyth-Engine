import os
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import threading
from contextlib import contextmanager

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("Warning: MCP libraries not installed. MCP functionality will be disabled.")

from basetool import BaseTool

@dataclass
class MCPServerConfig:
    """Configuration for an MCP server"""
    name: str
    command: List[str]
    args: List[str] = None
    env: Dict[str, str] = None
    timeout: int = 30
    auto_restart: bool = True
    enabled: bool = True

@dataclass
class MCPTool:
    """Represents a tool from an MCP server"""
    name: str
    description: str
    parameters: Dict[str, Any]
    server_name: str
    
class MCPToolWrapper(BaseTool):
    """Wrapper to make MCP tools compatible with existing BaseTool interface"""
    
    def __init__(self, mcp_tool: MCPTool, mcp_manager):
        self.mcp_tool = mcp_tool
        self.mcp_manager = mcp_manager
        
    @property
    def name(self) -> str:
        return self.mcp_tool.name
        
    @property
    def description(self) -> str:
        return self.mcp_tool.description
        
    @property
    def parameters(self) -> List[Dict[str, Any]]:
        # Convert MCP parameter schema to BaseTool format
        params = []
        if self.mcp_tool.parameters and 'properties' in self.mcp_tool.parameters:
            for param_name, param_info in self.mcp_tool.parameters['properties'].items():
                param_type = param_info.get('type', 'string')
                # Map JSON schema types to our types
                type_mapping = {
                    'string': 'string',
                    'integer': 'integer', 
                    'number': 'number',
                    'boolean': 'boolean',
                    'array': 'array',
                    'object': 'object'
                }
                params.append({
                    'name': param_name,
                    'type': type_mapping.get(param_type, 'string'),
                    'description': param_info.get('description', ''),
                    'required': param_name in self.mcp_tool.parameters.get('required', [])
                })
        return params
        
    @property
    def output_type(self) -> str:
        # Default to text_response, can be overridden based on tool name patterns
        tool_name = self.name.lower()
        if 'search' in tool_name and 'web' in tool_name:
            return 'web_search_results'
        elif 'image' in tool_name and 'search' in tool_name:
            return 'image_search_results'
        elif 'video' in tool_name and 'search' in tool_name:
            return 'video_search_results'
        elif 'generate' in tool_name and 'image' in tool_name:
            return 'generated_image'
        elif 'create' in tool_name and ('file' in tool_name or 'document' in tool_name):
            return 'downloadable_file'
        else:
            return 'text_response'
            
    def execute(self, **kwargs: Any) -> Any:
        return self.mcp_manager.execute_tool(self.mcp_tool.server_name, self.name, **kwargs)

class MCPManager:
    """Manages MCP server connections and tool execution"""
    
    def __init__(self, config_path: str = "mcp_config/", timeout: int = 30):
        self.config_path = config_path
        self.timeout = timeout
        self.servers: Dict[str, MCPServerConfig] = {}
        self.connections: Dict[str, Any] = {}
        self.tools: Dict[str, MCPTool] = {}  # tool_name -> MCPTool
        self.tool_to_server: Dict[str, str] = {}  # tool_name -> server_name
        self.lock = threading.RLock()
        self.logger = logging.getLogger(__name__)
        
        # Thread pool for async operations
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-")
        
        if not MCP_AVAILABLE:
            self.logger.warning("MCP libraries not available. MCP functionality disabled.")
            return
            
        # Load server configurations
        self._load_server_configs()
        
        # Initialize connections
        self._initialize_connections()
        
    def _load_server_configs(self):
        """Load MCP server configurations from config directory"""
        config_file = os.path.join(self.config_path, "servers.json")
        
        if not os.path.exists(config_file):
            # Create default config
            os.makedirs(self.config_path, exist_ok=True)
            default_config = {
                "servers": {
                    "filesystem": {
                        "name": "filesystem",
                        "command": ["npx", "@modelcontextprotocol/server-filesystem"],
                        "args": ["/tmp"],
                        "enabled": True
                    },
                    "brave_search": {
                        "name": "brave_search", 
                        "command": ["npx", "@modelcontextprotocol/server-brave-search"],
                        "env": {"BRAVE_API_KEY": ""},
                        "enabled": False
                    }
                }
            }
            with open(config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
                
        try:
            with open(config_file, 'r') as f:
                config_data = json.load(f)
                
            for server_name, server_config in config_data.get("servers", {}).items():
                self.servers[server_name] = MCPServerConfig(
                    name=server_config["name"],
                    command=server_config["command"],
                    args=server_config.get("args", []),
                    env=server_config.get("env", {}),
                    timeout=server_config.get("timeout", self.timeout),
                    auto_restart=server_config.get("auto_restart", True),
                    enabled=server_config.get("enabled", True)
                )
                
        except Exception as e:
            self.logger.error(f"Failed to load MCP server configs: {e}")
            
    def _initialize_connections(self):
        """Initialize connections to all enabled MCP servers"""
        if not MCP_AVAILABLE:
            return
            
        for server_name, config in self.servers.items():
            if config.enabled:
                try:
                    self._connect_to_server(server_name, config)
                except Exception as e:
                    self.logger.error(f"Failed to connect to MCP server {server_name}: {e}")
                    
    def _connect_to_server(self, server_name: str, config: MCPServerConfig):
        """Connect to a specific MCP server and discover its tools"""
        if not MCP_AVAILABLE:
            return
            
        try:
            # This is a simplified connection - in practice you'd use proper async handling
            server_params = StdioServerParameters(
                command=config.command[0],
                args=config.command[1:] + (config.args or []),
                env=config.env or {}
            )
            
            # Store connection info (in real implementation, maintain actual connection)
            self.connections[server_name] = {
                "params": server_params,
                "config": config,
                "connected": True,
                "last_heartbeat": None
            }
            
            # Discover tools from this server
            self._discover_server_tools(server_name)
            
            self.logger.info(f"Connected to MCP server: {server_name}")
            
        except Exception as e:
            self.logger.error(f"Failed to connect to server {server_name}: {e}")
            raise
            
    def _discover_server_tools(self, server_name: str):
        """Discover tools available from a specific MCP server"""
        # This is a placeholder - in real implementation, you'd query the server
        # For now, we'll simulate some common tools based on server type
        
        mock_tools = {
            "filesystem": [
                {
                    "name": "read_file",
                    "description": "Read contents of a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the file"}
                        },
                        "required": ["path"]
                    }
                },
                {
                    "name": "write_file", 
                    "description": "Write content to a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the file"},
                            "content": {"type": "string", "description": "Content to write"}
                        },
                        "required": ["path", "content"]
                    }
                }
            ],
            "brave_search": [
                {
                    "name": "web_search",
                    "description": "Search the web using Brave Search API",
                    "parameters": {
                        "type": "object", 
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "count": {"type": "integer", "description": "Number of results", "default": 10}
                        },
                        "required": ["query"]
                    }
                }
            ]
        }
        
        server_tools = mock_tools.get(server_name, [])
        
        for tool_info in server_tools:
            tool = MCPTool(
                name=tool_info["name"],
                description=tool_info["description"], 
                parameters=tool_info["parameters"],
                server_name=server_name
            )
            
            # Handle name conflicts by prefixing with server name
            tool_key = tool.name
            if tool_key in self.tools:
                tool_key = f"{server_name}_{tool.name}"
                
            self.tools[tool_key] = tool
            self.tool_to_server[tool_key] = server_name
            
        self.logger.info(f"Discovered {len(server_tools)} tools from server {server_name}")
        
    def get_available_tools(self) -> List[MCPTool]:
        """Get all available MCP tools"""
        with self.lock:
            return list(self.tools.values())
            
    def get_tool_wrappers(self) -> List[MCPToolWrapper]:
        """Get BaseTool-compatible wrappers for all MCP tools"""
        return [MCPToolWrapper(tool, self) for tool in self.get_available_tools()]
        
    def execute_tool(self, server_name: str, tool_name: str, **kwargs) -> Any:
        """Execute a tool on a specific MCP server"""
        if not MCP_AVAILABLE:
            raise RuntimeError("MCP libraries not available")
            
        with self.lock:
            if server_name not in self.connections:
                raise ValueError(f"No connection to server: {server_name}")
                
            if not self.connections[server_name]["connected"]:
                # Attempt to reconnect
                try:
                    config = self.servers[server_name]
                    self._connect_to_server(server_name, config)
                except Exception as e:
                    raise RuntimeError(f"Failed to reconnect to server {server_name}: {e}")
                    
        # In a real implementation, this would make an actual MCP call
        # For now, return a mock response based on tool type
        return self._mock_tool_execution(tool_name, **kwargs)
        
    def _mock_tool_execution(self, tool_name: str, **kwargs) -> Any:
        """Mock tool execution for development/testing"""
        if tool_name == "read_file":
            return {"content": f"Mock content for file: {kwargs.get('path', 'unknown')}", "success": True}
        elif tool_name == "write_file":
            return {"success": True, "message": f"Mock write to {kwargs.get('path', 'unknown')}"}
        elif tool_name == "web_search":
            return [
                {"title": f"Mock result for: {kwargs.get('query', 'unknown')}", 
                 "url": "https://example.com", 
                 "text": "Mock search result content"}
            ]
        else:
            return {"result": f"Mock execution of {tool_name}", "parameters": kwargs}
            
    def is_server_healthy(self, server_name: str) -> bool:
        """Check if a server connection is healthy"""
        with self.lock:
            connection = self.connections.get(server_name)
            return connection and connection.get("connected", False)
            
    def restart_server(self, server_name: str) -> bool:
        """Restart a specific MCP server"""
        if server_name not in self.servers:
            return False
            
        try:
            # Disconnect if connected
            if server_name in self.connections:
                del self.connections[server_name]
                
            # Remove tools from this server
            tools_to_remove = [k for k, v in self.tool_to_server.items() if v == server_name]
            for tool_key in tools_to_remove:
                del self.tools[tool_key]
                del self.tool_to_server[tool_key]
                
            # Reconnect
            config = self.servers[server_name]
            self._connect_to_server(server_name, config)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to restart server {server_name}: {e}")
            return False
            
    def shutdown(self):
        """Shutdown all MCP connections"""
        with self.lock:
            for server_name in list(self.connections.keys()):
                try:
                    del self.connections[server_name]
                except Exception as e:
                    self.logger.error(f"Error disconnecting from {server_name}: {e}")
                    
        self.executor.shutdown(wait=True)
        
    def __del__(self):
        """Cleanup on destruction"""
        try:
            self.shutdown()
        except:
            pass  # Ignore errors during cleanup