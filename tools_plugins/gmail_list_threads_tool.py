from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service

class GmailListThreadsTool(BaseTool):
    """
    A tool for listing recent email threads from the user's Gmail account.
    """

    @property
    def name(self) -> str:
        return "gmail_list_threads"

    @property
    def description(self) -> str:
        return "Searches and lists recent email threads from the user's inbox. Can be filtered with a search query."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "max_results", "type": "integer", "description": "The maximum number of email threads to return. Defaults to 5."},
            {"name": "query", "type": "string", "description": "A standard Gmail search query to filter emails (e.g., 'from:elon@musk.com', 'subject:weekly report', 'in:inbox'). Defaults to searching the inbox."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, max_results: int = 5, query: str = "in:inbox", **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            service = build_google_service(
                user_id=user_id,
                service_name='gmail',
                service_version='v1',
                scopes=['https://www.googleapis.com/auth/gmail.readonly']
            )

            results = service.users().threads().list(userId='me', maxResults=max_results, q=query).execute()
            threads = results.get('threads', [])

            if not threads:
                return {"message": "No email threads found matching your criteria."}

            thread_details = []
            for thread in threads:
                tdata = service.users().threads().get(userId='me', id=thread['id'], format='metadata', metadataHeaders=['Subject', 'From']).execute()
                msg = tdata['messages'][0]
                subject = ""
                sender = ""
                for header in msg['payload']['headers']:
                    if header['name'].lower() == 'subject':
                        subject = header['value']
                    if header['name'].lower() == 'from':
                        sender = header['value']
                
                thread_details.append({
                    "thread_id": thread['id'],
                    "subject": subject,
                    "sender": sender,
                    "snippet": tdata['messages'][0]['snippet']
                })
            
            return {"threads": thread_details}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Gmail list threads tool error: {e}")
            return {"error": f"An unexpected error occurred while listing Gmail threads: {str(e)}"}