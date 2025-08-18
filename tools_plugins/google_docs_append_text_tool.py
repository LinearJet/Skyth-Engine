from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service

class GoogleDocsAppendTextTool(BaseTool):
    """
    A tool for appending text to an existing Google Doc.
    """

    @property
    def name(self) -> str:
        return "google_docs_append_text"

    @property
    def description(self) -> str:
        return "Finds a Google Doc by name and appends new text to the end of it. Use for requests like 'add a new section to my doc' or 'append these notes to my meeting summary'."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "document_name", "type": "string", "description": "The exact name of the Google Doc to append to."},
            {"name": "text_to_append", "type": "string", "description": "The text content to add to the end of the document. Should start with a newline character if it's a new section."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, document_name: str, text_to_append: str, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            # Step 1: Find the document ID using the Drive API
            drive_service = build_google_service(
                user_id=user_id,
                service_name='drive',
                service_version='v3',
                scopes=['https://www.googleapis.com/auth/drive']
            )
            
            sanitized_name = document_name.replace("'", "\\'")
            query = f"name = '{sanitized_name}' and mimeType = 'application/vnd.google-apps.document' and trashed = false"
            
            results = drive_service.files().list(q=query, pageSize=1, fields="files(id, name)").execute()
            items = results.get('files', [])

            if not items:
                return {"error": f"Google Doc named '{document_name}' not found in your Drive."}
            
            document_id = items[0]['id']
            print(f"Found document '{document_name}' to append to with ID: {document_id}")

            # Step 2: Get the current document content to find the end index
            docs_service = build_google_service(
                user_id=user_id,
                service_name='docs',
                service_version='v1',
                scopes=['https://www.googleapis.com/auth/documents']
            )

            document = docs_service.documents().get(documentId=document_id).execute()
            end_index = document['body']['content'][-1]['endIndex'] - 1

            # Step 3: Append the text using a batchUpdate request
            requests = [
                {
                    'insertText': {
                        'location': {
                            'index': end_index,
                        },
                        'text': text_to_append
                    }
                }
            ]

            result = docs_service.documents().batchUpdate(
                documentId=document_id,
                body={'requests': requests}
            ).execute()

            print(f"Successfully appended text to '{document_name}'.")
            return {"success": f"Successfully appended text to the document '{document_name}'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Docs append tool error: {e}")
            return {"error": f"An unexpected error occurred while appending to the Google Doc: {str(e)}"}