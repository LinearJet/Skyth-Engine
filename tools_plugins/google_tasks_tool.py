from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import dateparser

class GoogleTasksTool(BaseTool):
    """
    A universal tool for managing Google Tasks. It can list, create, delete, or complete tasks.
    The 'action' parameter determines the operation.
    """

    @property
    def name(self) -> str:
        return "google_tasks"

    @property
    def description(self) -> str:
        return "Manages Google Tasks. Use 'list' to see tasks, 'create' to add a task, 'delete' to remove a task, and 'complete' to mark a task as done."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "action", "type": "string", "description": "The operation to perform: 'list', 'create', 'delete', or 'complete'."},
            {"name": "task_title", "type": "string", "description": "The title of the task. Required for 'create', 'delete', and 'complete'."},
            {"name": "notes", "type": "string", "description": "Additional details for the task. Used with 'create'."},
            {"name": "due_date", "type": "string", "description": "The due date in natural language (e.g., 'tomorrow'). Used with 'create'."},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, action: str, task_title: str = None, notes: str = None, due_date: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided."}

        try:
            service = build_google_service(
                user_id=user_id,
                service_name='tasks',
                service_version='v1',
                scopes=['https://www.googleapis.com/auth/tasks']
            )

            if action == 'list':
                return self._list_tasks(service)
            elif action == 'create':
                if not task_title:
                    return {"error": "For 'create' action, 'task_title' is required."}
                return self._create_task(service, task_title, notes, due_date)
            elif action == 'delete' or action == 'complete':
                if not task_title:
                    return {"error": f"For '{action}' action, 'task_title' is required to find the task."}
                return self._update_task_status(service, task_title, action)
            else:
                return {"error": f"Invalid action '{action}'. Must be 'list', 'create', 'delete', or 'complete'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Tasks tool error: {e}")
            return {"error": f"An unexpected error occurred with Google Tasks: {str(e)}"}

    def _list_tasks(self, service) -> Dict[str, Any]:
        results = service.tasks().list(tasklist='@default', showCompleted=False).execute()
        items = results.get('items', [])
        if not items:
            return {"message": "You have no pending tasks."}
        task_titles = [item['title'] for item in items]
        return {"tasks": task_titles}

    def _create_task(self, service, title, notes, due_date_description) -> Dict[str, Any]:
        task = {'title': title}
        if notes:
            task['notes'] = notes
        if due_date_description:
            due = dateparser.parse(due_date_description, settings={'PREFER_DATES_FROM': 'future'})
            if due:
                task['due'] = due.isoformat() + "Z"
        
        created_task = service.tasks().insert(tasklist='@default', body=task).execute()
        return {"success": f"Task '{created_task.get('title')}' was successfully created."}

    def _find_task_by_title(self, service, title: str) -> Dict[str, Any]:
        """Helper to find a task by its title."""
        results = service.tasks().list(tasklist='@default', showCompleted=False, showHidden=True).execute()
        tasks = results.get('items', [])
        for task in tasks:
            if task.get('title', '').strip().lower() == title.strip().lower():
                return task
        return None

    def _update_task_status(self, service, title: str, action: str) -> Dict[str, Any]:
        task_to_update = self._find_task_by_title(service, title)
        if not task_to_update:
            return {"error": f"No pending task found with the title '{title}'."}

        task_id = task_to_update['id']

        if action == 'delete':
            service.tasks().delete(tasklist='@default', task=task_id).execute()
            return {"success": f"Successfully deleted the task: '{title}'."}
        
        if action == 'complete':
            task_to_update['status'] = 'completed'
            service.tasks().update(tasklist='@default', task=task_id, body=task_to_update).execute()
            return {"success": f"Successfully marked the task '{title}' as complete."}
        
        return {"error": "Invalid update action."} # Should not be reached