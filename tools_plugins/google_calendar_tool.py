from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import dateutil.parser as parser
import datetime

class GoogleCalendarTool(BaseTool):
    """
    A universal tool for managing Google Calendar. It can list, create, or delete events.
    The 'action' parameter determines the operation.
    """

    @property
    def name(self) -> str:
        return "google_calendar"

    @property
    def description(self) -> str:
        return "Manages Google Calendar events. Use 'list' to see upcoming events, 'create' to add a new event, and 'delete' to remove an event."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "action", "type": "string", "description": "The operation to perform: 'list', 'create', or 'delete'."},
            {"name": "event_name", "type": "string", "description": "The name/summary of the event. Required for 'create' and 'delete'."},
            {"name": "start_time", "type": "string", "description": "The start time in ISO 8601 format. Required for 'create'."},
            {"name": "end_time", "type": "string", "description": "The end time in ISO 8601 format. Required for 'create'."},
            {"name": "timezone", "type": "string", "description": "The user's timezone (e.g., 'America/New_York'). Required for 'create'."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, action: str, event_name: str = None, start_time: str = None, end_time: str = None, timezone: str = 'UTC', **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided."}

        try:
            service = build_google_service(
                user_id=user_id,
                service_name='calendar',
                service_version='v3',
                scopes=['https://www.googleapis.com/auth/calendar']
            )

            if action == 'list':
                return self._list_events(service)
            elif action == 'create':
                if not all([event_name, start_time, end_time, timezone]):
                    return {"error": "For 'create' action, 'event_name', 'start_time', 'end_time', and 'timezone' are required."}
                return self._create_event(service, event_name, start_time, end_time, timezone)
            elif action == 'delete':
                if not event_name:
                    return {"error": "For 'delete' action, 'event_name' is required to find the event."}
                return self._delete_event(service, event_name)
            else:
                return {"error": f"Invalid action '{action}'. Must be 'list', 'create', or 'delete'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Calendar tool error: {e}")
            return {"error": f"An unexpected error occurred with Google Calendar: {str(e)}"}

    def _list_events(self, service, max_results=10) -> Dict[str, Any]:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=max_results, singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return {"message": "No upcoming events found."}
        
        formatted_events = [{"summary": e['summary'], "start_time": e['start'].get('dateTime', e['start'].get('date'))} for e in events]
        return {"events": formatted_events}

    def _create_event(self, service, summary, start_time_iso, end_time_iso, timezone) -> Dict[str, Any]:
        event = {
            'summary': summary,
            'start': {'dateTime': start_time_iso, 'timeZone': timezone},
            'end': {'dateTime': end_time_iso, 'timeZone': timezone},
        }
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return {"success": f"Event '{summary}' created successfully.", "event_url": created_event.get('htmlLink')}

    def _delete_event(self, service, event_name) -> Dict[str, Any]:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            q=event_name, maxResults=1, singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        if not events:
            return {"error": f"No upcoming event found with the name '{event_name}'."}
        
        event_to_delete = events[0]
        service.events().delete(calendarId='primary', eventId=event_to_delete['id']).execute()
        return {"success": f"Successfully deleted the event: '{event_to_delete['summary']}'."}