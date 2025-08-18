from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service

class GoogleTasksListTool(BaseTool):
    """
    A tool for listing tasks from the user's Google Tasks.
    """

    @property
    def name(self) -> str:
        return "google_tasks_list"

    @property
    def description(self) -> str:
        return "Retrieves a list of tasks from the user's default task list. Use for questions like 'what are my tasks?' or 'show my to-do list'."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [] # No parameters needed to list tasks from the default list

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, **kwargs) -> Dict[str, Any]:
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

            # Fetch tasks from the default list that are not yet completed
            results = service.tasks().list(tasklist='@default', showCompleted=False).execute()
            items = results.get('items', [])

            if not items:
                return {"message": "You have no pending tasks on your default list."}

            task_titles = [item['title'] for item in items]
            return {"tasks": task_titles}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Tasks list tool error: {e}")
            return {"error": f"An unexpected error occurred while listing tasks: {str(e)}"}