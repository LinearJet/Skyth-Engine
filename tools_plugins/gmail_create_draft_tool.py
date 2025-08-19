from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
from email.mime.text import MIMEText
import base64

class GmailCreateDraftTool(BaseTool):
    """
    A tool for creating a new email draft in Gmail. Can also be used to reply to a thread.
    """

    @property
    def name(self) -> str:
        return "gmail_create_draft"

    @property
    def description(self) -> str:
        return "Creates a new email draft. Can be used for new emails or for replying to an existing email thread if a thread_id is provided."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "to", "type": "string", "description": "The recipient's email address."},
            {"name": "subject", "type": "string", "description": "The subject line of the email."},
            {"name": "body", "type": "string", "description": "The main content/body of the email."},
            {"name": "thread_id", "type": "string", "description": "The ID of the email thread to reply to. If omitted, a new email thread is created. (Optional)"},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, to: str, subject: str, body: str, thread_id: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            service = build_google_service(
                user_id=user_id,
                service_name='gmail',
                service_version='v1',
                scopes=['https://www.googleapis.com/auth/gmail.compose']
            )

            message = MIMEText(body)
            message['to'] = to
            message['subject'] = subject
            
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            draft_body = {'message': {'raw': raw_message}}
            if thread_id:
                draft_body['message']['threadId'] = thread_id

            draft = service.users().drafts().create(userId='me', body=draft_body).execute()
            
            print(f"Draft created with ID: {draft['id']}")
            return {"success": f"Draft created successfully for '{subject}'. The user can now review it in Gmail."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Gmail create draft tool error: {e}")
            return {"error": f"An unexpected error occurred while creating the draft: {str(e)}"}