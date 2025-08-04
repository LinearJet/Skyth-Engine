from basetool import BaseTool
from typing import Dict, Any, List

class TextUtilityTool(BaseTool):
    """
    A simple tool for performing various text manipulations.
    """

    @property
    def name(self) -> str:
        return "text_utility"

    @property
    def description(self) -> str:
        return "Performs text manipulations like reversing, word counting, character counting, or changing case. Use this when the user asks to perform a specific string operation."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "text_input", "type": "string", "description": "The text to be manipulated."},
            {"name": "operation", "type": "string", "description": "The operation to perform. Must be one of: 'reverse', 'word_count', 'char_count', 'uppercase', 'lowercase'."},
            {"name": "char_to_count", "type": "string", "description": "The specific character to count. Only required when the operation is 'char_count'."}
        ]

    @property
    def output_type(self) -> str:
        # This is a custom output type. The generic pipeline will handle it as a simple text response.
        return "text_utility_result"

    def execute(self, text_input: str, operation: str, char_to_count: str = None) -> Dict[str, Any]:
        """
        Executes the specified text manipulation.
        """
        operation = operation.lower()
        
        if operation == 'reverse':
            result = text_input[::-1]
        elif operation == 'word_count':
            result = len(text_input.split())
        elif operation == 'uppercase':
            result = text_input.upper()
        elif operation == 'lowercase':
            result = text_input.lower()
        elif operation == 'char_count':
            if not char_to_count:
                return {"error": "The 'char_to_count' parameter is required for the 'char_count' operation."}
            # Perform a case-insensitive count for robustness
            result = text_input.lower().count(char_to_count.lower())
        else:
            return {"error": f"Invalid operation '{operation}'. Must be one of 'reverse', 'word_count', 'char_count', 'uppercase', 'lowercase'."}
            
        return {
            "operation": operation,
            "original_text": text_input,
            "result": result
        }