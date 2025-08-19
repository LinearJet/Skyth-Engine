from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import base64
from email.mime.text import MIMEText
from bs4 import BeautifulSoup

def _parse_email_body(parts: List[Dict[str, Any]]) -> str:
    """Recursively parses the payload of a Gmail message to find the plain text body, falling back to HTML."""
    if not parts:
        return ""
    
    # Pass 1: Look for text/plain first
    for part in parts:
        if part.get('mimeType') == 'text/plain':
            encoded_body = part.get('body', {}).get('data', '')
            if encoded_body:
                return base64.urlsafe_b64decode(encoded_body).decode('utf-8', errors='ignore')
        elif 'parts' in part:
            plain_text = _parse_email_body(part['parts'])
            if plain_text:
                return plain_text

    # Pass 2: Fall back to HTML
    for part in parts:
        if part.get('mimeType') == 'text/html':
            encoded_body = part.get('body', {}).get('data', '')
            if encoded_body:
                html_content = base64.urlsafe_b64decode(encoded_body).decode('utf-8', errors='ignore')
                soup = BeautifulSoup(html_content, 'html.parser')
                return soup.get_text(separator='\n', strip=True)
        elif 'parts' in part:
            html_text = _parse_email_body(part['parts'])
            if html_text:
                return html_text
    return ""

class GoogleGmailTool(BaseTool):
    """
    A universal tool for managing Gmail. It can list, read, create drafts, and send emails.
    The 'action' parameter determines the operation.
    """

    @property
    def name(self) -> str:
        return "google_gmail"

    @property
    def description(self) -> str:
        return "Manages Gmail. Use 'list' to search/list emails, 'read' to get an email's content, 'create_draft' to write a draft, and 'send' to send a draft."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "action", "type": "string", "description": "The operation: 'list', 'read', 'create_draft', or 'send'."},
            {"name": "query", "type": "string", "description": "Gmail search query. Used with 'list'."},
            {"name": "thread_id", "type": "string", "description": "The ID of the email thread. Used with 'read' and for replies in 'create_draft'."},
            {"name": "draft_id", "type": "string", "description": "The ID of the draft to send. Required for 'send'."},
            {"name": "to", "type": "string", "description": "Recipient's email. Used with 'create_draft'."},
            {"name": "subject", "type": "string", "description": "Email subject. Used with 'create_draft'."},
            {"name": "body", "type": "string", "description": "Email body. Used with 'create_draft'."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, action: str, query: str = "in:inbox", thread_id: str = None, draft_id: str = None, to: str = None, subject: str = None, body: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided."}

        try:
            # Determine the correct scope based on the action
            scope = 'https://www.googleapis.com/auth/gmail.modify' if action in ['create_draft', 'send'] else 'https://www.googleapis.com/auth/gmail.readonly'
            
            service = build_google_service(
                user_id=user_id,
                service_name='gmail',
                service_version='v1',
                scopes=[scope]
            )

            if action == 'list':
                return self._list_threads(service, query)
            elif action == 'read':
                if not thread_id: return {"error": "A 'thread_id' is required to read an email."}
                return self._read_thread(service, thread_id)
            elif action == 'create_draft':
                if not all([to, subject, body]): return {"error": "'to', 'subject', and 'body' are required to create a draft."}
                return self._create_draft(service, to, subject, body, thread_id)
            elif action == 'send':
                if not draft_id: return {"error": "A 'draft_id' is required to send an email."}
                return self._send_draft(service, draft_id)
            else:
                return {"error": f"Invalid action '{action}'. Must be 'list', 'read', 'create_draft', or 'send'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Gmail tool error: {e}")
            return {"error": f"An unexpected error occurred with Gmail: {str(e)}"}

    def _list_threads(self, service, query, max_results=5) -> Dict[str, Any]:
        results = service.users().threads().list(userId='me', maxResults=max_results, q=query).execute()
        threads = results.get('threads', [])
        if not threads:
            return {"message": "No email threads found."}
        
        thread_details = []
        for thread in threads:
            tdata = service.users().threads().get(userId='me', id=thread['id'], format='metadata', metadataHeaders=['Subject', 'From']).execute()
            msg = tdata['messages'][0]
            subject = next((h['value'] for h in msg['payload']['headers'] if h['name'].lower() == 'subject'), "")
            sender = next((h['value'] for h in msg['payload']['headers'] if h['name'].lower() == 'from'), "")
            thread_details.append({"thread_id": thread['id'], "subject": subject, "sender": sender, "snippet": msg['snippet']})
        return {"threads": thread_details}

    def _read_thread(self, service, thread_id) -> Dict[str, Any]:
        thread = service.users().threads().get(userId='me', id=thread_id).execute()
        latest_message = thread['messages'][-1]
        payload = latest_message.get('payload', {})
        subject = next((h['value'] for h in payload.get('headers', []) if h['name'].lower() == 'subject'), "")
        sender = next((h['value'] for h in payload.get('headers', []) if h['name'].lower() == 'from'), "")
        
        body = ""
        if 'parts' in payload:
            body = _parse_email_body(payload['parts'])
        elif 'body' in payload and payload['body'].get('data'):
            body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
        
        return {"subject": subject, "from": sender, "content": body.strip()}

    def _create_draft(self, service, to, subject, body, thread_id) -> Dict[str, Any]:
        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        draft_body = {'message': {'raw': raw_message}}
        if thread_id:
            draft_body['message']['threadId'] = thread_id
        
        draft = service.users().drafts().create(userId='me', body=draft_body).execute()
        return {"success": f"Draft created successfully for '{subject}'.", "draft_id": draft['id']}

    def _send_draft(self, service, draft_id) -> Dict[str, Any]:
        service.users().drafts().send(userId='me', body={'id': draft_id}).execute()
        return {"success": f"Draft with ID '{draft_id}' has been sent successfully."}