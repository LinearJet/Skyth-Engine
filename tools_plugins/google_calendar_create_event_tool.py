from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import dateutil.parser as parser

class GoogleCalendarCreateEventTool(BaseTool):
    """
    A tool for creating events in the user's Google Calendar.
    """

    @property
    def name(self) -> str:
        return "google_calendar_create_event"

    @property
    def description(self) -> str:
        return "Creates a new event in the user's primary Google Calendar. Requires a summary (title), start time, and end time in ISO 8601 format."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "summary", "type": "string", "description": "The title or summary of the event."},
            {"name": "start_time", "type": "string", "description": "The start date and time of the event in ISO 8601 format (e.g., '2025-08-31T09:00:00')."},
            {"name": "end_time", "type": "string", "description": "The end date and time of the event in ISO 8601 format (e.g., '2025-08-31T15:00:00')."},
            {"name": "timezone", "type": "string", "description": "The user's timezone, e.g., 'America/New_York' or 'Europe/London'."},
            {"name": "description", "type": "string", "description": "A detailed description for the event. (Optional)"},
            {"name": "location", "type": "string", "description": "The location of the event. (Optional)"},
        ]

    @property
    def output_type(self) -> str:
        return "text_response"

    def execute(self, summary: str, start_time: str, end_time: str, timezone: str, description: str = None, location: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            # The agent is now responsible for providing a valid ISO string.
            start_dt = parser.isoparse(start_time)
            end_dt = parser.isoparse(end_time)
        except ValueError as e:
            return {"error": f"Invalid date format provided by the agent. Details: {e}"}

        try:
            service = build_google_service(
                user_id=user_id,
                service_name='calendar',
                service_version='v3',
                scopes=['https://www.googleapis.com/auth/calendar']
            )

            event = {
                'summary': summary,
                'location': location,
                'description': description,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': timezone,
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': timezone,
                },
            }

            created_event = service.events().insert(calendarId='primary', body=event).execute()
            print(f"Event created: {created_event.get('htmlLink')}")
            
            return {"success": f"Event '{summary}' was successfully created.", "event_url": created_event.get('htmlLink')}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Calendar tool error: {e}")
            return {"error": f"An unexpected error occurred while creating the calendar event: {str(e)}"}