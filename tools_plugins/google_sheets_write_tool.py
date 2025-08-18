from basetool import BaseTool
from typing import Dict, Any, List
from .google_api_utils import build_google_service

class GoogleSheetsWriteTool(BaseTool):
    """
    A tool for writing data to a specified range in a Google Sheet.
    """

    @property
    def name(self) -> str:
        return "google_sheets_write"

    @property
    def description(self) -> str:
        return "Finds a Google Sheet by name and writes data to it. The data should be a list of lists, where each inner list represents a row."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {"name": "sheet_name", "type": "string", "description": "The exact name of the Google Sheet to write to."},
            {"name": "cell_range", "type": "string", "description": "The starting cell for the data to be written (e.g., 'A1', 'Sheet2!B3'). Defaults to 'A1' of the first sheet."},
            {"name": "data", "type": "string", "description": "A JSON string representing a list of lists. Example: '[[\"Name\", \"Score\"], [\"Alice\", 100]]'"},
        ]

    @property
    def output_type(self) -> str:
        return "json_response"

    def execute(self, sheet_name: str, data: str, cell_range: str = 'A1', **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get('user_id')
        if not user_id:
            return {"error": "Authentication error: User ID not provided to the tool."}

        try:
            # Step 1: Find the spreadsheet ID using the Drive API
            drive_service = build_google_service(
                user_id=user_id,
                service_name='drive',
                service_version='v3',
                scopes=['https://www.googleapis.com/auth/drive.readonly'] # Readonly is enough to find files
            )
            
            sanitized_name = sheet_name.replace("'", "\\'")
            query = f"name = '{sanitized_name}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
            
            results = drive_service.files().list(q=query, pageSize=1, fields="files(id, name)").execute()
            items = results.get('files', [])

            if not items:
                return {"error": f"Google Sheet named '{sheet_name}' not found in your Drive."}
            
            spreadsheet_id = items[0]['id']
            print(f"Found spreadsheet '{sheet_name}' with ID: {spreadsheet_id}")

            # Step 2: Write data to the sheet using the Sheets API
            sheets_service = build_google_service(
                user_id=user_id,
                service_name='sheets',
                service_version='v4',
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )

            # The model provides data as a JSON string, so we need to parse it
            import json
            try:
                values = json.loads(data)
                if not isinstance(values, list) or not all(isinstance(row, list) for row in values):
                    raise ValueError("Data must be a list of lists.")
            except (json.JSONDecodeError, ValueError) as e:
                return {"error": f"Invalid data format. The 'data' parameter must be a JSON string of a list of lists. Error: {e}"}

            body = {
                'values': values
            }
            
            result = sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=cell_range,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()

            print(f"Successfully wrote {result.get('updatedCells')} cells to '{sheet_name}'.")
            return {"success": f"Successfully wrote {result.get('updatedCells')} cells to the spreadsheet '{sheet_name}'."}

        except ConnectionRefusedError as e:
            return {"error": str(e)}
        except Exception as e:
            print(f"Google Sheets write tool error: {e}")
            return {"error": f"An unexpected error occurred while writing to the Google Sheet: {str(e)}"}