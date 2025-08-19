from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service
import json

class GoogleSheetsTool(BaseTool):
    """
    A universal tool for managing Google Sheets. It can create sheets and write data.
    The 'action' parameter determines the operation.
    """

    @property
    def name(self) -> str:
        return "google_sheets"

    @property
    def description(self) -> str:
        return "Manages Google Sheets. Use 'create' to make a new sheet, and 'write' to add data to an existing sheet."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "action", "type": "string", "description": "The operation to perform: 'create' or 'write'."},
            {"name": "sheet_name", "type": "string", "description": "The name/title of the spreadsheet. Required for all actions."},
            {"name": "cell_range", "type": "string", "description": "The starting cell for the data to be written (e.g., 'A1', 'Sheet2!B3'). Used with 'write'."},
            {"name": "data", "type": "string", "description": "A JSON string representing a list of lists for the 'write' action. Example: '[[\"Name\", \"Score\"], [\"Alice\", 100]]'"},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, action: str, sheet_name: str, cell_range: str = 'A1', data: str = None, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided."}

        try:
            if action == 'create':
                return self._create_sheet(user_id, sheet_name)
            elif action == 'write':
                if not data:
                    return {"error": "For 'write' action, 'data' is required."}
                return self._write_to_sheet(user_id, sheet_name, cell_range, data)
            else:
                return {"error": f"Invalid action '{action}'. Must be 'create' or 'write'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Sheets tool error: {e}")
            return {"error": f"An unexpected error occurred with Google Sheets: {str(e)}"}

    def _create_sheet(self, user_id: int, title: str) -> Dict[str, Any]:
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive'])
        file_metadata = {'name': title, 'mimeType': 'application/vnd.google-apps.spreadsheet'}
        file = drive_service.files().create(body=file_metadata, fields='id, name, webViewLink').execute()
        return {"success": f"Successfully created Google Sheet titled '{title}'.", "spreadsheet_url": file.get('webViewLink')}

    def _write_to_sheet(self, user_id: int, sheet_name: str, cell_range: str, data_json: str) -> Dict[str, Any]:
        # Step 1: Find the spreadsheet using the Drive API
        drive_service = build_google_service(user_id, 'drive', 'v3', ['https://www.googleapis.com/auth/drive.readonly'])
        sanitized_name = sheet_name.replace("'", "\\'")
        query = f"name = '{sanitized_name}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
        results = drive_service.files().list(q=query, pageSize=1, fields="files(id, name)").execute()
        items = results.get('files', [])
        if not items:
            return {"error": f"Google Sheet named '{sheet_name}' not found."}
        
        spreadsheet_id = items[0]['id']
        
        # Step 2: Build the Sheets service with the CORRECT, EXPLICIT scope
        sheets_service = build_google_service(
            user_id,
            'sheets',
            'v4',
            ['https://www.googleapis.com/auth/spreadsheets'] # <-- THE DEFINITIVE FIX
        )
        
        try:
            values = json.loads(data_json)
            if not isinstance(values, list) or not all(isinstance(row, list) for row in values):
                raise ValueError("Data must be a list of lists.")
        except (json.JSONDecodeError, ValueError) as e:
            return {"error": f"Invalid data format. Error: {e}"}

        body = {'values': values}
        result = sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=cell_range,
            valueInputOption='USER_ENTERED', body=body
        ).execute()
        
        return {"success": f"Successfully wrote {result.get('updatedCells')} cells to '{sheet_name}'."}