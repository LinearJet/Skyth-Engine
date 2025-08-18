import os
import importlib
import inspect
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
        Executes a tool with the given parameters, intelligently filtering kwargs
        based on whether the tool's execute method accepts a variable keyword argument (**kwargs).
        """
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Tool '{name}' not found.")

        # --- FINAL, ROBUST ARGUMENT HANDLING ---
        sig = inspect.signature(tool.execute)
        
        # Check if the tool's execute method has a **kwargs parameter
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )

        if has_var_keyword:
            # If the tool is flexible (has **kwargs), pass all arguments through.
            # This allows tools to receive context like user_id and timezone.
            return tool.execute(**kwargs)
        else:
            # If the tool is strict (no **kwargs), filter to only the accepted parameters.
            # This prevents TypeErrors for tools like web_search.
            accepted_params = sig.parameters.keys()
            filtered_kwargs = {k: v for k, v in kwargs.items() if k in accepted_params}
            return tool.execute(**filtered_kwargs)
        # --- END FINAL FIX ---

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