from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import base64
from bs4 import BeautifulSoup # <-- NEW IMPORT

def _parse_email_body(parts: List[Dict[str, Any]]) -> str:
    """
    Recursively parses the payload of a Gmail message to find the plain text body.
    Falls back to parsing HTML if no plain text is found.
    """
    if not parts:
        return ""
    
    # --- NEW: Two-pass parsing strategy ---
    # Pass 1: Look for text/plain first for the cleanest result.
    for part in parts:
        if part.get('mimeType') == 'text/plain':
            encoded_body = part.get('body', {}).get('data', '')
            if encoded_body:
                return base64.urlsafe_b64decode(encoded_body).decode('utf-8', errors='ignore')
        elif 'parts' in part:
            # Recursively search in nested parts
            plain_text = _parse_email_body(part['parts'])
            if plain_text:
                return plain_text

    # Pass 2: If no plain text was found, fall back to HTML.
    for part in parts:
        if part.get('mimeType') == 'text/html':
            encoded_body = part.get('body', {}).get('data', '')
            if encoded_body:
                html_content = base64.urlsafe_b64decode(encoded_body).decode('utf-8', errors='ignore')
                soup = BeautifulSoup(html_content, 'html.parser')
                return soup.get_text(separator='\n', strip=True)
        elif 'parts' in part:
            html_text = _parse_email_body(part['parts']) # This will now also check for HTML
            if html_text:
                return html_text

    return "" # Return empty if nothing is found

class GmailReadEmailTool(BaseTool):
    """
    A tool for reading the content of a specific email thread.
    """

    @property
    def name(self) -> str:
        return "gmail_read_email"

    @property
    def description(self) -> str:
        return "Reads the full content of a specific email thread using its thread ID. Use this to get details after finding an email with the list tool."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "thread_id", "type": "string", "description": "The ID of the email thread to read."},
        ]

    @property
    def output_type(self) -> str:
        return "text_content"

    def execute(self, thread_id: str, **kwargs) -> Dict[str, Any]:
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

            thread = service.users().threads().get(userId='me', id=thread_id).execute()
            if not thread or 'messages' not in thread:
                return {"error": f"Could not find email thread with ID: {thread_id}"}

            latest_message = thread['messages'][-1]
            payload = latest_message.get('payload', {})
            
            subject = ""
            sender = ""
            for header in payload.get('headers', []):
                if header['name'].lower() == 'subject':
                    subject = header['value']
                if header['name'].lower() == 'from':
                    sender = header['value']

            body = ""
            if 'parts' in payload:
                body = _parse_email_body(payload['parts'])
            elif 'body' in payload and payload['body'].get('data'):
                encoded_body = payload['body']['data']
                body = base64.urlsafe_b64decode(encoded_body).decode('utf-8', errors='ignore')

            return {
                "subject": subject,
                "from": sender,
                "content": body.strip()
            }

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Gmail read email tool error: {e}")
            return {"error": f"An unexpected error occurred while reading the email: {str(e)}"}