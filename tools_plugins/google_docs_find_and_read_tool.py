from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service

class GoogleDocsFindAndReadTool(BaseTool):
    """
    A tool to intelligently find and read a Google Doc using keywords.
    """

    @property
    def name(self) -> str:
        return "google_docs_find_and_read"

    @property
    def description(self) -> str:
        return "Searches the user's Google Drive for a document using keywords and reads its content. Use this when the user asks to retrieve information from a document, e.g., 'find my dark fantasy story' or 'pull up the notes on 13BR'."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "search_keywords", "type": "string", "description": "Keywords from the user's query to find the document (e.g., 'dark fantasy story', '13BR Everything notes')."},
        ]

    @property
    def output_type(self) -> str:
        return "text_content"

    def execute(self, search_keywords: str, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            drive_service = build_google_service(
                user_id=user_id,
                service_name='drive',
                service_version='v3',
                scopes=['https://www.googleapis.com/auth/drive'] # <-- FIX: Align with the powerful scope from config.py
            )
            
            sanitized_keywords = search_keywords.replace("'", "\\'")
            
            broad_query = f"(name contains '{sanitized_keywords}' or fullText contains '{sanitized_keywords}') and mimeType = 'application/vnd.google-apps.document' and trashed = false"
            results = drive_service.files().list(q=broad_query, pageSize=10, fields="files(id, name, webViewLink)").execute()
            items = results.get('files', [])

            if not items:
                return {"error": f"No Google Docs found matching the keywords: '{search_keywords}'."}

            exact_match = None
            for item in items:
                if item['name'].strip().lower() == search_keywords.strip().lower():
                    exact_match = item
                    break
            
            if exact_match:
                items = [exact_match]
            
            if len(items) > 1:
                possible_files = [item['name'] for item in items]
                return {
                    "clarification_needed": "Multiple documents found. Please ask the user to specify which one they meant.",
                    "options": possible_files
                }

            document_id = items[0]['id']
            document_name = items[0]['name']
            document_link = items[0].get('webViewLink')
            print(f"Found single document match '{document_name}' with ID: {document_id}")

            docs_service = build_google_service(
                user_id=user_id,
                service_name='docs',
                service_version='v1',
                scopes=['https://www.googleapis.com/auth/documents'] # This is correct as it's covered by the main scope
            )

            document = docs_service.documents().get(documentId=document_id).execute()
            content = document.get('body').get('content')
            
            text_content = _read_structural_elements(content)
            
            return {
                "document_name": document_name,
                "content": text_content.strip(),
                "url": document_link
            }

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Docs tool error: {e}")
            return {"error": f"An unexpected error occurred while accessing Google Docs: {str(e)}"}

# This helper function should already be in your file, but ensure it's there.
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