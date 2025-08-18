from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service

class GoogleDocsCreateTool(BaseTool):
    """
    A tool for creating a new, blank Google Doc.
    """

    @property
    def name(self) -> str:
        return "google_docs_create"

    @property
    def description(self) -> str:
        return "Creates a new, blank Google Doc with a specified title in the user's Google Drive."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "title", "type": "string", "description": "The title for the new Google Doc."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, title: str, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            drive_service = build_google_service(
                user_id=user_id,
                service_name='drive',
                service_version='v3',
                scopes=['https://www.googleapis.com/auth/drive']
            )

            file_metadata = {
                'name': title,
                'mimeType': 'application/vnd.google-apps.document'
            }
            
            file = drive_service.files().create(body=file_metadata, fields='id, name, webViewLink').execute()
            
            doc_url = file.get('webViewLink')
            print(f"Google Doc created: {file.get('name')} ({doc_url})")
            
            return {"success": f"Successfully created Google Doc titled '{title}'.", "document_url": doc_url}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Docs create tool error: {e}")
            return {"error": f"An unexpected error occurred while creating the Google Doc: {str(e)}"}