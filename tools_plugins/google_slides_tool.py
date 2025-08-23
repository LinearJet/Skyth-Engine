from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import uuid

class GoogleSlidesTool(BaseTool):
    """
    A tool for managing Google Slides. It can create presentations, add slides with text, and insert images.
    The 'action' parameter determines the operation.
    """

    @property
    def name(self) -> str:
        return "google_slides"

    @property
    def description(self) -> str:
        return "Manages Google Slides presentations. Use 'create' to make a new presentation, 'add_slide' to add a new slide with a title and body, and 'add_image' to insert an image from a URL onto the last slide."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "action", "type": "string", "description": "The operation to perform: 'create', 'add_slide', or 'add_image'."},
            {"name": "presentation_name", "type": "string", "description": "The name/title of the presentation. Required for all actions."},
            {"name": "title", "type": "string", "description": "The title for a new slide. Only used with the 'add_slide' action."},
            {"name": "body", "type": "string", "description": "The body text for a new slide, with new lines for bullet points. Only used with 'add_slide' action."},
            {"name": "image_url", "type": "string", "description": "The public URL of the image to insert. Only used with the 'add_image' action."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, action: str, presentation_name: str, title: str = None, body: str = None, image_url: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided."}

        try:
            if action == 'create':
                return self._create_presentation(user_id, presentation_name)
            elif action == 'add_slide':
                if not title or not body:
                    return {"error": "For 'add_slide' action, 'title' and 'body' are required."}
                return self._add_slide(user_id, presentation_name, title, body)
            elif action == 'add_image':
                if not image_url:
                    return {"error": "For 'add_image' action, 'image_url' is required."}
                return self._add_image(user_id, presentation_name, image_url)
            else:
                return {"error": f"Invalid action '{action}'. Must be 'create', 'add_slide', or 'add_image'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Slides tool error: {e}")
            return {"error": f"An unexpected error occurred with Google Slides: {str(e)}"}

    def _find_presentation_by_name(self, drive_service, name: str) -> Dict[str, Any]:
        """Helper to find a presentation and return its metadata or None."""
        sanitized_name = name.replace("'", "\\'")
        query = f"name = '{sanitized_name}' and mimeType = 'application/vnd.google-apps.presentation' and trashed = false"
        results = drive_service.files().list(q=query, pageSize=1, fields="files(id, name, webViewLink)").execute()
        items = results.get('files', [])
        return items[0] if items else None

    def _create_presentation(self, user_id: int, title: str) -> Dict[str, Any]:
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive'])
        file_metadata = {'name': title, 'mimeType': 'application/vnd.google-apps.presentation'}
        file = drive_service.files().create(body=file_metadata, fields='id, name, webViewLink').execute()
        return {"success": f"Successfully created Google Slides presentation titled '{title}'.", "presentation_url": file.get('webViewLink')}

    def _add_slide(self, user_id: int, presentation_name: str, title: str, body: str) -> Dict[str, Any]:
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive.readonly'])
        presentation_file = self._find_presentation_by_name(drive_service, presentation_name)
        if not presentation_file:
            return {"error": f"Presentation named '{presentation_name}' not found."}
        
        presentation_id = presentation_file['id']
        slides_service = build_google_service(user_id, 'slides', 'v1', ['https://www.googleapis.com/auth/presentations'])
        
        new_slide_id = uuid.uuid4().hex
        title_shape_id = uuid.uuid4().hex
        body_shape_id = uuid.uuid4().hex

        requests = [
            {
                'createSlide': {
                    'objectId': new_slide_id,
                    'slideLayoutReference': {'predefinedLayout': 'TITLE_AND_BODY'},
                    'placeholderIdMappings': [
                        {'layoutPlaceholder': {'type': 'TITLE'}, 'objectId': title_shape_id},
                        {'layoutPlaceholder': {'type': 'BODY'}, 'objectId': body_shape_id},
                    ],
                }
            },
            {'insertText': {'objectId': title_shape_id, 'text': title}},
            {'insertText': {'objectId': body_shape_id, 'text': body}},
        ]
        
        slides_service.presentations().batchUpdate(presentationId=presentation_id, body={'requests': requests}).execute()
        return {"success": f"Successfully added a new slide titled '{title}' to '{presentation_name}'."}

    def _add_image(self, user_id: int, presentation_name: str, image_url: str) -> Dict[str, Any]:
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive.readonly'])
        presentation_file = self._find_presentation_by_name(drive_service, presentation_name)
        if not presentation_file:
            return {"error": f"Presentation named '{presentation_name}' not found."}
            
        presentation_id = presentation_file['id']
        slides_service = build_google_service(user_id, 'slides', 'v1', ['https://www.googleapis.com/auth/presentations'])

        presentation = slides_service.presentations().get(presentationId=presentation_id).execute()
        slides = presentation.get('slides', [])
        if not slides:
            return {"error": "The presentation has no slides to add an image to."}
        
        last_slide_id = slides[-1]['objectId']
        new_image_id = uuid.uuid4().hex

        requests = [
            {
                'createImage': {
                    'objectId': new_image_id,
                    'url': image_url,
                    'elementProperties': {
                        'pageObjectId': last_slide_id,
                        'size': {'height': {'magnitude': 200, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}},
                        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 100, 'translateY': 100, 'unit': 'PT'}
                    }
                }
            }
        ]
        
        slides_service.presentations().batchUpdate(presentationId=presentation_id, body={'requests': requests}).execute()
        return {"success": f"Successfully added an image to the last slide of '{presentation_name}'."}