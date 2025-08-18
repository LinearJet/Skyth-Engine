from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import datetime

class GoogleCalendarListEventsTool(BaseTool):
    """
    A tool for listing upcoming events from the user's Google Calendar.
    """

    @property
    def name(self) -> str:
        return "google_calendar_list_events"

    @property
    def description(self) -> str:
        return "Retrieves a list of the user's upcoming events from their primary Google Calendar."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "max_results", "type": "integer", "description": "The maximum number of events to return. Defaults to 10."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, max_results: int = 10, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            service = build_google_service(
                user_id=user_id,
                service_name='calendar',
                service_version='v3',
                scopes=['https://www.googleapis.com/auth/calendar.readonly']
            )

            now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
            
            events_result = service.events().list(
                calendarId='primary', timeMin=now,
                maxResults=max_results, singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])

            if not events:
                return {"message": "No upcoming events found."}

            formatted_events = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                formatted_events.append({
                    "summary": event['summary'],
                    "start_time": start
                })
            
            return {"events": formatted_events}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Calendar list events tool error: {e}")
            return {"error": f"An unexpected error occurred while reading calendar events: {str(e)}"}