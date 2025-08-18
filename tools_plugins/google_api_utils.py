import sqlite3
import time
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from config import app, DATABASE # <-- CORRECTED IMPORT

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def build_google_service(user_id: int, service_name: str, service_version: str, scopes: list):
    """
    Builds an authorized Google API service object for a specific user.
    Handles token fetching, validation, and refreshing.
    """
    if not user_id:
        raise ValueError("User ID is required to build a Google service.")

    conn = get_db_connection()
    user_creds_row = conn.execute('SELECT google_access_token, google_refresh_token, google_token_expires_at FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()

    if not user_creds_row or not user_creds_row['google_refresh_token']:
        raise ConnectionRefusedError("User has not connected their Google Workspace account or is missing a refresh token. Please connect via the profile page.")

    creds = Credentials(
        token=user_creds_row['google_access_token'],
        refresh_token=user_creds_row['google_refresh_token'],
        token_uri='https://oauth2.googleapis.com/token',
        client_id=app.config['GOOGLE_CLIENT_ID'], # <-- CORRECTED ACCESS
        client_secret=app.config['GOOGLE_CLIENT_SECRET'], # <-- CORRECTED ACCESS
        scopes=scopes
    )

    if creds and creds.expired and creds.refresh_token:
        print(f"[Google API] Credentials for user {user_id} expired. Refreshing...")
        creds.refresh(Request())
        
        # Persist the new credentials back to the database
        conn = get_db_connection()
        conn.execute(
            'UPDATE users SET google_access_token = ?, google_token_expires_at = ? WHERE id = ?',
            (creds.token, creds.expiry.timestamp(), user_id)
        )
        conn.commit()
        conn.close()
        print(f"[Google API] Credentials for user {user_id} refreshed and saved.")

    try:
        service = build(service_name, service_version, credentials=creds)
        print(f"[Google API] Successfully built '{service_name}' service for user {user_id}.")
        return service
    except Exception as e:
        print(f"[Google API] Failed to build service '{service_name}' for user {user_id}: {e}")
        raise e