import os
import json
import logging
import datetime
import gspread
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import AuthorizedSession, Request
from config import Config

logger = logging.getLogger(__name__)

def get_service_account_info():
    """Retrieves service account dict from env JSON string or JSON file."""
    if Config.GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            return json.loads(Config.GOOGLE_SERVICE_ACCOUNT_JSON)
        except Exception as e:
            logger.error(f"Error parsing GOOGLE_SERVICE_ACCOUNT_JSON: {e}")
            return None

    if Config.GOOGLE_SERVICE_ACCOUNT_FILE and os.path.exists(Config.GOOGLE_SERVICE_ACCOUNT_FILE):
        try:
            with open(Config.GOOGLE_SERVICE_ACCOUNT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading GOOGLE_SERVICE_ACCOUNT_FILE: {e}")
            return None

    return None

def get_google_credentials():
    """Generates google.oauth2 credentials with drive & sheets scopes for Service Account."""
    info = get_service_account_info()
    if not info:
        return None
    try:
        return Credentials.from_service_account_info(info, scopes=Config.SCOPES)
    except Exception as e:
        logger.error(f"Failed to build Google Credentials: {e}")
        return None

def get_oauth_client_config():
    """
    Retrieves Google OAuth 2.0 Web Client ID and Client Secret.
    Priority:
    1. System_Config sheet in Google Sheets (persisted across Vercel deployments).
    2. Environment variables.
    """
    client_id = None
    client_secret = None

    try:
        from services.sheets_db import SheetsDB
        client_id = SheetsDB.get_system_config('google_user_client_id')
        client_secret = SheetsDB.get_system_config('google_user_client_secret')
    except Exception as e:
        logger.debug(f"Could not read client config from System_Config: {e}")

    if not client_id:
        client_id = getattr(Config, 'GOOGLE_USER_CLIENT_ID', None) or os.environ.get('GOOGLE_USER_CLIENT_ID')
    if not client_secret:
        client_secret = getattr(Config, 'GOOGLE_USER_CLIENT_SECRET', None) or os.environ.get('GOOGLE_USER_CLIENT_SECRET')

    return (client_id.strip() if client_id else None,
            client_secret.strip() if client_secret else None)

def get_user_oauth_credentials():
    """
    Checks for configured User OAuth 2.0 credentials (human user with personal Drive quota).
    Priority:
    1. System_Config worksheet in Google Sheets (persistent across Vercel serverless restarts).
    2. Environment variables (GOOGLE_USER_REFRESH_TOKEN, etc.).
    3. Local config file (Config.GOOGLE_USER_OAUTH_FILE).
    """
    client_id, client_secret = get_oauth_client_config()
    refresh_token = None
    access_token = None

    # 1. Check Google Sheets System_Config
    try:
        from services.sheets_db import SheetsDB
        refresh_token = SheetsDB.get_system_config('google_user_refresh_token')
        access_token = SheetsDB.get_system_config('google_user_access_token')
    except Exception as e:
        logger.debug(f"Could not read user tokens from System_Config: {e}")

    # 2. Check environment variables
    if not refresh_token:
        refresh_token = getattr(Config, 'GOOGLE_USER_REFRESH_TOKEN', None) or os.environ.get('GOOGLE_USER_REFRESH_TOKEN')

    # 3. Check local file if present
    if not (client_id and client_secret and refresh_token):
        oauth_file = getattr(Config, 'GOOGLE_USER_OAUTH_FILE', None)
        if oauth_file and os.path.exists(oauth_file):
            try:
                with open(oauth_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    access_token = data.get('token') or access_token
                    refresh_token = data.get('refresh_token') or refresh_token
                    client_id = data.get('client_id') or client_id
                    client_secret = data.get('client_secret') or client_secret
            except Exception as e:
                logger.warning(f"Error reading GOOGLE_USER_OAUTH_FILE ({oauth_file}): {e}")

    if client_id and client_secret and refresh_token:
        try:
            creds = UserCredentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=client_id,
                client_secret=client_secret,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            # Verify or refresh if expired
            if not creds.valid or not access_token:
                creds.refresh(Request())
                try:
                    from services.sheets_db import SheetsDB
                    SheetsDB.set_system_config('google_user_access_token', creds.token, updated_by='oauth_auto_refresh')
                    if creds.expiry:
                        SheetsDB.set_system_config('google_user_token_expiry', creds.expiry.strftime('%Y-%m-%d %H:%M:%S'), updated_by='oauth_auto_refresh')
                except Exception as save_err:
                    logger.debug(f"Could not persist refreshed access token to Sheets: {save_err}")
            return creds
        except Exception as e:
            logger.warning(f"Error building/refreshing UserCredentials: {e}")

    return None

def get_drive_credentials():
    """
    Returns credentials for Google Drive operations:
    1. User OAuth (human quota) if configured and valid.
    2. Service Account (supports Shared Drives).
    """
    user_creds = get_user_oauth_credentials()
    if user_creds:
        return user_creds
    return get_google_credentials()

def get_gspread_client():
    """Returns an authorized gspread Client instance (always uses Service Account)."""
    creds = get_google_credentials()
    if not creds:
        raise ValueError("Google Service Account credentials not available.")
    return gspread.authorize(creds)

def get_drive_session():
    """Returns an AuthorizedSession for direct Google Drive REST API calls."""
    creds = get_drive_credentials()
    if not creds:
        raise ValueError("Google Drive credentials not available.")
    return AuthorizedSession(creds)

def sanitize_cell_value(val):
    """
    Prevents CSV / Formula injection in Google Sheets.
    If a string starts with =, +, -, @, \t, or \r, prepend a single quote (').
    """
    if val is None:
        return ''
    val_str = str(val)
    if val_str and val_str[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + val_str
    return val_str

def sanitize_row(row_list):
    """Sanitizes an entire row list for safe insertion into Google Sheets."""
    return [sanitize_cell_value(cell) for cell in row_list]

def get_drive_status_diagnostic(folder_id=None):
    """
    Comprehensive diagnostic reporting for Google Drive integration,
    supporting both User OAuth (personal quota) and Service Account.
    """
    fid = folder_id or Config.GOOGLE_DRIVE_FOLDER_ID
    user_creds = get_user_oauth_credentials()
    sa_creds = get_google_credentials()
    sa_info = get_service_account_info()
    sa_email = sa_info.get('client_email') if sa_info else None

    client_id, client_secret = get_oauth_client_config()
    user_email = None
    try:
        from services.sheets_db import SheetsDB
        user_email = SheetsDB.get_system_config('google_user_email')
    except Exception:
        pass

    has_user_oauth = user_creds is not None
    active_creds = user_creds if has_user_oauth else sa_creds

    drive_verified = False
    drive_verified_at = None
    try:
        from services.sheets_db import SheetsDB
        drive_verified = str(SheetsDB.get_system_config('drive_verified_success', 'FALSE')).upper() == 'TRUE'
        drive_verified_at = SheetsDB.get_system_config('drive_last_verified_at')
    except Exception:
        pass

    diag = {
        'folder_id': fid,
        'folder_url': f"https://drive.google.com/drive/folders/{fid}",
        'folder_name': None,
        'has_folder_access': False,
        'is_shared_drive': False,
        'can_add_children': False,
        'can_upload_binary': False,
        'drive_verified_success': drive_verified,
        'drive_last_verified_at': drive_verified_at,
        'auth_mode': 'user_oauth' if has_user_oauth else 'service_account',
        'active_account_email': user_email or ('Authorized User (OAuth)' if has_user_oauth else sa_email),
        'service_account_email': sa_email,
        'client_id_configured': bool(client_id),
        'client_secret_configured': bool(client_secret),
        'has_refresh_token': bool(has_user_oauth),
        'user_oauth_email': user_email,
        'storage_quota': None,
        'status_code': 200,
        'message': ''
    }

    if not active_creds:
        diag['message'] = "No Google credentials configured."
        return diag

    session = AuthorizedSession(active_creds)

    # 1. Fetch user storage quota & info if User OAuth is active
    if has_user_oauth:
        try:
            about_url = "https://www.googleapis.com/drive/v3/about?fields=user,storageQuota"
            about_resp = session.get(about_url)
            if about_resp.status_code == 200:
                about_data = about_resp.json()
                u_info = about_data.get('user', {})
                if not user_email and u_info.get('emailAddress'):
                    user_email = u_info.get('emailAddress')
                    diag['user_oauth_email'] = user_email
                    diag['active_account_email'] = user_email
                    try:
                        from services.sheets_db import SheetsDB
                        SheetsDB.set_system_config('google_user_email', user_email, updated_by='oauth_verify')
                    except Exception:
                        pass
                quota = about_data.get('storageQuota', {})
                limit_bytes = int(quota.get('limit', 0)) if quota.get('limit') else None
                usage_bytes = int(quota.get('usage', 0)) if quota.get('usage') else None
                diag['storage_quota'] = {
                    'limit_bytes': limit_bytes,
                    'usage_bytes': usage_bytes,
                    'limit_gb': round(limit_bytes / (1024**3), 2) if limit_bytes else 'Unlimited',
                    'usage_gb': round(usage_bytes / (1024**3), 2) if usage_bytes else 0
                }
        except Exception as e:
            logger.warning(f"Could not fetch Drive about info: {e}")

    # 2. Check target folder access
    try:
        url = f"https://www.googleapis.com/drive/v3/files/{fid}?supportsAllDrives=true&fields=id,name,mimeType,capabilities,driveId,shared"
        resp = session.get(url)
        if resp.status_code == 200:
            data = resp.json()
            diag['has_folder_access'] = True
            diag['folder_name'] = data.get('name')
            diag['can_add_children'] = data.get('capabilities', {}).get('canAddChildren', True)
            diag['is_shared_drive'] = bool(data.get('driveId'))

            if has_user_oauth:
                if drive_verified:
                    diag['can_upload_binary'] = True
                    diag['message'] = (
                        f"Connected & Verified via User OAuth ({diag['active_account_email']}). "
                        f"Live probe upload/download verified in folder '{data.get('name')}'. Direct uploads enabled."
                    )
                else:
                    diag['can_upload_binary'] = False
                    diag['message'] = (
                        f"User OAuth authorized for {diag['active_account_email']}. "
                        "Live binary upload/download test has not run yet. Click 'Test Live Upload' to verify folder write access and activate uploads."
                    )
            elif diag['is_shared_drive']:
                diag['can_upload_binary'] = True
                diag['message'] = (
                    f"Connected via Service Account to Google Workspace Shared Drive. "
                    f"Access confirmed to folder '{data.get('name')}'. Binary uploads enabled under Shared Drive quota."
                )
            else:
                diag['can_upload_binary'] = False
                diag['message'] = (
                    f"Connected to folder '{data.get('name')}' via Service Account. "
                    "However, this folder is on a Personal Google Drive where Service Accounts have 0 bytes storage quota. "
                    "Please authorize Google Drive with your personal Gmail account via Google OAuth below to enable direct binary uploads."
                )
        elif resp.status_code == 404:
            diag['has_folder_access'] = False
            diag['can_upload_binary'] = False
            diag['status_code'] = 404
            diag['message'] = (
                f"Drive folder '{fid}' not found or not shared with '{diag['active_account_email']}'. "
                f"Please verify folder ID and share it as Editor in Google Drive."
            )
        else:
            diag['has_folder_access'] = False
            diag['can_upload_binary'] = False
            diag['status_code'] = resp.status_code
            diag['message'] = f"Drive API returned HTTP {resp.status_code}: {resp.text}"
    except Exception as e:
        diag['has_folder_access'] = False
        diag['can_upload_binary'] = False
        diag['message'] = f"Error checking Drive folder: {str(e)}"

    return diag

def check_drive_folder_access(folder_id=None):
    """
    Backwards-compatible wrapper returning (has_access: bool, details: dict).
    """
    diag = get_drive_status_diagnostic(folder_id)
    return diag['has_folder_access'], diag
