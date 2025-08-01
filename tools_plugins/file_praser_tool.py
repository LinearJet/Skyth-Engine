import base64
import io
import pypdf
from basetool import BaseTool
from typing import Dict, Any, List

class FileParserTool(BaseTool):
    """
    A tool for parsing the content of various file types.
    """

    @property
    def name(self) -> str:
        return "file_parser"

    @property
    def description(self) -> str:
        return "Parses the content of an uploaded file (PDF, TXT, etc.) and returns its text content."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "file_data", "type": "string", "description": "The base64 encoded string of the file content."},
            {"name": "file_name", "type": "string", "description": "The name of the file, including its extension."}
        ]

    @property
    def output_type(self) -> str:
        return "file_content"

    def execute(self, file_data: str, file_name: str) -> Dict[str, Any]:
        """
        Parses the file content. Returns a dict with 'text_content' or 'error'.
        """
        try:
            decoded_bytes = base64.b64decode(file_data)
            file_content = ""
            
            if file_name and file_name.lower().endswith('.pdf'):
                pdf_reader = pypdf.PdfReader(io.BytesIO(decoded_bytes))
                content_parts = [page.extract_text() for page in pdf_reader.pages]
                file_content = "\n\n".join(content_parts)
                if not file_content.strip():
                    return {"error": "Could not extract text from this PDF. It may be an image-based PDF."}
            else:
                try:
                    file_content = decoded_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    file_content = decoded_bytes.decode('latin-1', errors='replace')
                if not file_content.strip():
                     return {"error": "File appears to be empty or in an unreadable binary format."}
            
            return {"text_content": file_content}

        except Exception as e:
            print(f"File processing error for {file_name}: {e}")
            return {"error": f"An error occurred while processing the file: {str(e)}"}
