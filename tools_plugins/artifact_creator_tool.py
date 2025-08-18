import base64
import mimetypes
from basetool import BaseTool
from typing import Dict, Any, List

class ArtifactCreatorTool(BaseTool):
    """
    A sophisticated tool for creating various downloadable file artifacts.
    It can package text, HTML, code, or base64-encoded image data into a file.
    """

    @property
    def name(self) -> str:
        return "artifact_creator"

    @property
    def description(self) -> str:
        return (
            "Creates a downloadable file artifact from provided content. "
            "Use this to save text, code, reports, or images for the user. "
            "For example: 'save the summary as report.md', 'create a python script from this code', "
            "or 'package the generated image as a downloadable file'."
        )

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "filename",
                "type": "string",
                "description": "The desired filename, including the extension (e.g., 'notes.txt', 'chart.html', 'image.png')."
            },
            {
                "name": "content",
                "type": "string",
                "description": "The content for the file. This can be plain text, HTML code, or base64-encoded data for binary files like images."
            },
            {
                "name": "encoding",
                "type": "string",
                "description": "The encoding of the content. Use 'text' for plain text/code/html, or 'base64' if the content is already base64-encoded (e.g., for images)."
            }
        ]

    @property
    def output_type(self) -> str:
        return "downloadable_file"

    def execute(self, filename: str, content: str, encoding: str = 'text') -> Dict[str, Any]:
        """
        Encodes the file content into a downloadable data URI based on the specified encoding.
        """
        try:
            mime_type, _ = mimetypes.guess_type(filename)
            if mime_type is None:
                mime_type = 'application/octet-stream'

            b64_content = ""
            if encoding == 'base64':
                # The content is already base64, just use it directly.
                b64_content = content
            elif encoding == 'text':
                # The content is text, encode it to bytes then to base64.
                content_bytes = content.encode('utf-8')
                b64_content = base64.b64encode(content_bytes).decode('utf-8')
            else:
                return {"error": f"Unsupported encoding type: '{encoding}'. Use 'text' or 'base64'."}

            data_uri = f"data:{mime_type};base64,{b64_content}"

            # The UI expects this specific dictionary structure for downloadable files.
            return {
                "type": "downloadable_file",
                "filename": filename,
                "mime_type": mime_type,
                "data_uri": data_uri,
                "title": f"Download: {filename}"
            }
        except Exception as e:
            print(f"Artifact creation tool error: {e}")
            return {"error": f"An unexpected error occurred while creating the file: {str(e)}"}