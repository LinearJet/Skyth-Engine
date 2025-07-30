from abc import ABC, abstractmethod
from typing import Any, Dict, List

class BaseTool(ABC):
    """
    An abstract base class for all tool plugins.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Returns the unique, machine-readable name of the tool.
        """
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """
        Returns a detailed, natural language description of the tool.
        """
        pass

    @property
    @abstractmethod
    def parameters(self) -> List[Dict[str, Any]]:
        """
        Returns a schema describing the inputs the tool accepts.
        """
        pass

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """
        Executes the tool's logic with the given parameters.
        """
        pass