from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service

def _read_structural_elements(elements: List[Dict[str, Any]]) -> str:
    """
    Recursively reads text from a list of Google Docs StructuralElement objects.
    This handles paragraphs, tables, and other structures.
    """
    text = ""
    for value in elements:
        if 'paragraph' in value:
            for elem in value.get('paragraph').get('elements'):
                if 'textRun' in elem:
                    text += elem.get('textRun').get('content')
        elif 'table' in value:
            table = value.get('table')
            for row in table.get('tableRows'):
                for cell in row.get('tableCells'):
                    text += _read_structural_elements(cell.get('content'))
                text += '\n'
        elif 'tableOfContents' in value:
            toc = value.get('tableOfContents')
            text += _read_structural_elements(toc.get('content'))
    return text

class GoogleDocsTool(BaseTool):
    """
    A universal tool for managing Google Docs. It can create, read, or append to documents.
    The 'action' parameter determines the operation.
    """

    @property
    def name(self) -> str:
        return "google_docs"

    @property
    def description(self) -> str:
        return "Manages Google Docs. Use 'create' to make a new doc, 'read' to find and read a doc's content, and 'append' to add text to the end of a doc."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "action", "type": "string", "description": "The operation to perform: 'create', 'read', or 'append'."},
            {"name": "document_name", "type": "string", "description": "The name/title of the document. Required for all actions."},
            {"name": "content_to_append", "type": "string", "description": "The text content to add to the end of the document. Only used with the 'append' action."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, action: str, document_name: str, content_to_append: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided."}

        try:
            if action == 'create':
                return self._create_doc(user_id, document_name)
            elif action == 'read':
                return self._read_doc(user_id, document_name)
            elif action == 'append':
                if not content_to_append:
                    return {"error": "For 'append' action, 'content_to_append' is required."}
                return self._append_to_doc(user_id, document_name, content_to_append)
            else:
                return {"error": f"Invalid action '{action}'. Must be 'create', 'read', or 'append'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Docs tool error: {e}")
            return {"error": f"An unexpected error occurred with Google Docs: {str(e)}"}

    def _find_doc_by_name(self, drive_service, doc_name: str) -> Dict[str, Any]:
        """Helper to find a doc and return its metadata or None."""
        sanitized_name = doc_name.replace("'", "\\'")
        query = f"name = '{sanitized_name}' and mimeType = 'application/vnd.google-apps.document' and trashed = false"
        results = drive_service.files().list(q=query, pageSize=1, fields="files(id, name, webViewLink)").execute()
        items = results.get('files', [])
        return items[0] if items else None

    def _create_doc(self, user_id: int, title: str) -> Dict[str, Any]:
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive'])
        file_metadata = {'name': title, 'mimeType': 'application/vnd.google-apps.document'}
        file = drive_service.files().create(body=file_metadata, fields='id, name, webViewLink').execute()
        doc_url = file.get('webViewLink')
        return {"success": f"Successfully created Google Doc titled '{title}'.", "document_url": doc_url}

    def _read_doc(self, user_id: int, doc_name: str) -> Dict[str, Any]:
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive'])
        doc_file = self._find_doc_by_name(drive_service, doc_name)
        if not doc_file:
            return {"error": f"Google Doc named '{doc_name}' not found."}
        
        document_id = doc_file['id']
        docs_service = build_google_service(user_id, 'docs', 'v1', ['https://www.googleapis.com/auth/documents'])
        document = docs_service.documents().get(documentId=document_id).execute()
        content = document.get('body').get('content')
        text_content = _read_structural_elements(content)
        
        return {
            "document_name": doc_name,
            "content": text_content.strip(),
            "url": doc_file.get('webViewLink')
        }

    def _append_to_doc(self, user_id: int, doc_name: str, text_to_append: str) -> Dict[str, Any]:
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive'])
        doc_file = self._find_doc_by_name(drive_service, doc_name)
        if not doc_file:
            return {"error": f"Google Doc named '{doc_name}' not found."}

        document_id = doc_file['id']
        docs_service = build_google_service(user_id, 'docs', 'v1', ['https://www.googleapis.com/auth/documents'])
        
        document = docs_service.documents().get(documentId=document_id).execute()
        end_index = document['body']['content'][-1]['endIndex'] - 1

        requests = [{'insertText': {'location': {'index': end_index}, 'text': text_to_append}}]
        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
        
        return {"success": f"Successfully appended text to the document '{doc_name}'."}