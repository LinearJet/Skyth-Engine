from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import dateparser
from datetime import datetime

class GoogleTasksCreateTool(BaseTool):
    """
    A tool for creating a new task in the user's Google Tasks.
    """

    @property
    def name(self) -> str:
        return "google_tasks_create"

    @property
    def description(self) -> str:
        return "Creates a new task in the user's default task list. Use for requests like 'remind me to...' or 'add a to-do...'"

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "title", "type": "string", "description": "The title of the task."},
            {"name": "notes", "type": "string", "description": "Additional details or notes for the task. (Optional)"},
            {"name": "due_date_description", "type": "string", "description": "The due date in natural language (e.g., 'tomorrow', 'next Friday at 5pm'). (Optional)"},
        ]

    @property
    def output_type(self) -> str:
        return "text_response"

    def execute(self, title: str, notes: str = None, due_date_description: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            service = build_google_service(
                user_id=user_id,
                service_name='tasks',
                service_version='v1',
                scopes=['https://www.googleapis.com/auth/tasks']
            )

            task = {'title': title}
            if notes:
                task['notes'] = notes
            
            if due_date_description:
                # Parse the natural language due date
                due_date = dateparser.parse(due_date_description, settings={'PREFER_DATES_FROM': 'future'})
                if due_date:
                    # Format for the Tasks API (RFC 3339 format)
                    task['due'] = due_date.isoformat() + "Z"

            # Insert the task into the default task list
            created_task = service.tasks().insert(tasklist='@default', body=task).execute()
            print(f"Task created: {created_task.get('title')}")
            
            return {"success": f"Task '{title}' was successfully created."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Tasks create tool error: {e}")
            return {"error": f"An unexpected error occurred while creating the task: {str(e)}"}