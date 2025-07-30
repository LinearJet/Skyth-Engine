import os
import importlib
from typing import Dict, List, Optional
from basetool import BaseTool

class ToolRegistry:
    """
    A registry for discovering and managing tool plugins.
    """

    def __init__(self, plugins_dir: str = "tools_plugins"):
        self.plugins_dir = plugins_dir
        self.tools: Dict[str, BaseTool] = {}
        self._discover_plugins()

    def _discover_plugins(self):
        """
        Discovers and loads tool plugins from the specified directory.
        """
        if not os.path.exists(self.plugins_dir):
            return

        for filename in os.listdir(self.plugins_dir):
            if filename.endswith("_tool.py"):
                module_name = filename[:-3]
                module_path = f"{self.plugins_dir}.{module_name}"
                try:
                    module = importlib.import_module(module_path)
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and issubclass(attr, BaseTool) and attr is not BaseTool:
                            tool_instance = attr()
                            self.tools[tool_instance.name] = tool_instance
                            print(f"Loaded tool: {tool_instance.name}")
                except Exception as e:
                    print(f"Failed to load tool from {module_path}: {e}")

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """
        Retrieves a tool by its name.
        """
        return self.tools.get(name)

    def get_all_tools(self) -> List[BaseTool]:
        """
        Returns a list of all available tools.
        """
        return list(self.tools.values())

    def execute_tool(self, name: str, **kwargs) -> any:
        """
        Executes a tool with the given parameters.
        """
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Tool '{name}' not found.")
        return tool.execute(**kwargs)

# Example usage:
if __name__ == "__main__":
    registry = ToolRegistry()
    print("\nAvailable tools:")
    for tool in registry.get_all_tools():
        print(f"- {tool.name}: {tool.description}")

    # Example of executing a tool
    try:
        search_results = registry.execute_tool("web_search", query="latest AI news")
        print("\nWeb search results:")
        print(search_results)
    except Exception as e:
        print(f"\nError executing web_search: {e}")