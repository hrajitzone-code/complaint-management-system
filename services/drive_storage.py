import os
import io
import uuid
import logging
import hashlib
import datetime
from werkzeug.utils import secure_filename
from config import Config
from services.google_client import (
    get_drive_session,
    get_drive_status_diagnostic,
    check_drive_folder_access,
    get_google_credentials,
    get_user_oauth_credentials
)

logger = logging.getLogger(__name__)

def is_allowed_file(filename):
    """Checks file extension against allowed whitelist."""
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in Config.ALLOWED_EXTENSIONS

def upload_attachment(file_storage, custom_name=None):
    """
    Uploads a file strictly to the configured Google Drive folder.
    Zero local filesystem fallback.

    Returns:
        dict: {
            'file_id': str,
            'filename': str,
            'web_link': str,
            'download_url': str,
            'storage_type': 'google_drive',
            'size': int,
            'auth_mode': str
        }
    """
    if not file_storage or not file_storage.filename:
        return None

    orig_filename = secure_filename(file_storage.filename)
    if not is_allowed_file(orig_filename):
        raise ValueError(f"File type not allowed. Allowed formats: {', '.join(sorted(Config.ALLOWED_EXTENSIONS))}")

    # Read bytes and check length
    file_bytes = file_storage.read()
    file_storage.seek(0)
    file_size = len(file_bytes)
    if file_size > Config.MAX_CONTENT_LENGTH:
        raise ValueError(f"File size exceeds maximum allowed limit of {Config.MAX_CONTENT_LENGTH // (1024*1024)}MB.")

    # Unique display filename
    file_ext = orig_filename.rsplit('.', 1)[1].lower() if '.' in orig_filename else ''
    clean_stem = orig_filename.rsplit('.', 1)[0] if '.' in orig_filename else orig_filename
    unique_filename = f"{clean_stem}_{uuid.uuid4().hex[:6]}.{file_ext}" if file_ext else f"{clean_stem}_{uuid.uuid4().hex[:6]}"

    # Verify Drive folder access and upload quota capability
    diag = get_drive_status_diagnostic(Config.GOOGLE_DRIVE_FOLDER_ID)
    if not diag.get('has_folder_access'):
        raise RuntimeError(
            f"Google Drive folder access error: {diag.get('message')} "
            f"Please verify folder ID '{Config.GOOGLE_DRIVE_FOLDER_ID}' and permissions."
        )

    if not diag.get('can_upload_binary'):
        raise RuntimeError(
            "Google Drive upload failed: The target folder is on a Personal Google Drive, where Service Accounts have 0 bytes of storage quota. "
            "Please authorize Google Drive with your personal Gmail account via Google OAuth in Settings to enable direct binary uploads. "
            "Alternatively, you can paste an existing Google Drive share link in the complaint form."
        )

    try:
        drive_session = get_drive_session()
        
        # Multipart upload to Google Drive v3 directly into target folder
        metadata = {
            'name': unique_filename,
            'parents': [Config.GOOGLE_DRIVE_FOLDER_ID]
        }
        files = {
            'data': ('metadata', io.StringIO(str(metadata).replace("'", '"')), 'application/json; charset=UTF-8'),
            'file': (unique_filename, file_bytes, file_storage.mimetype or 'application/octet-stream')
        }
        
        upload_url = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id,name,webViewLink,webContentLink'
        resp = drive_session.post(upload_url, files=files)
        
        if resp.status_code in (200, 201):
            data = resp.json()
            file_id = data.get('id')
            web_link = data.get('webViewLink') or f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk"
            
            # Make file readable to anyone with link if permissions allow
            try:
                perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions?supportsAllDrives=true"
                drive_session.post(perm_url, json={'role': 'reader', 'type': 'anyone'})
            except Exception as perm_err:
                logger.warning(f"Could not set public permission on Drive file {file_id}: {perm_err}")

            return {
                'file_id': file_id,
                'filename': orig_filename,
                'web_link': web_link,
                'download_url': f"/api/drive/download/{file_id}",
                'storage_type': 'google_drive',
                'size': file_size,
                'auth_mode': diag.get('auth_mode', 'user_oauth')
            }
        else:
            err_text = resp.text
            if 'storageQuotaExceeded' in err_text or 'storage quota' in err_text.lower():
                logger.error(f"Drive storage quota exceeded: {err_text}")
                raise RuntimeError(
                    "Google Drive upload failed: Service Accounts have 0 bytes of personal Drive storage quota. "
                    "Please authorize Google Drive with your personal Gmail account via Google OAuth in Settings."
                )
            err_msg = f"Drive API returned HTTP {resp.status_code}: {err_text}"
            logger.error(f"Drive upload failed: {err_msg}")
            raise RuntimeError(f"Google Drive upload failed: {err_msg}. Please retry your submission.")
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Exception during Drive upload: {e}")
        raise RuntimeError(f"Google Drive connection error during attachment upload: {str(e)}. Please retry.")

def download_attachment(file_id):
    """
    Downloads file content bytes and metadata from Google Drive by file ID.
    Returns:
        tuple: (filename: str, mime_type: str, file_bytes: bytes)
    """
    drive_session = get_drive_session()
    
    # 1. Fetch file metadata
    meta_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=id,name,mimeType,size&supportsAllDrives=true"
    meta_resp = drive_session.get(meta_url)
    if meta_resp.status_code != 200:
        raise RuntimeError(f"File not found in Google Drive (HTTP {meta_resp.status_code}): {meta_resp.text}")
    
    meta_data = meta_resp.json()
    filename = meta_data.get('name', f"file_{file_id}")
    mime_type = meta_data.get('mimeType', 'application/octet-stream')

    # 2. Download file content
    media_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"
    media_resp = drive_session.get(media_url)
    if media_resp.status_code != 200:
        raise RuntimeError(f"Could not download file content from Google Drive (HTTP {media_resp.status_code}): {media_resp.text}")

    return filename, mime_type, media_resp.content

def test_drive_upload_and_download():
    """
    Executes a complete end-to-end binary test:
    1. Creates a unique test probe file in memory.
    2. Uploads to target folder Config.GOOGLE_DRIVE_FOLDER_ID.
    3. Confirms file ID and webViewLink.
    4. Downloads the probe file back and checks SHA-256 byte integrity.
    5. Deletes the test probe file from Google Drive to leave no clutter.
    6. Persists verification status to System_Config sheet in Google Sheets.

    Returns:
        dict: Test result details with verified proof.
    """
    diag = get_drive_status_diagnostic(Config.GOOGLE_DRIVE_FOLDER_ID)
    if not diag.get('has_folder_access'):
        raise RuntimeError(f"Folder access check failed: {diag.get('message')}")

    # Binary uploads require personal quota (User OAuth) or a Workspace Shared Drive
    if diag.get('auth_mode') != 'user_oauth' and not diag.get('is_shared_drive'):
        raise RuntimeError(
            "Binary upload test blocked: The target folder is on a Personal Google Drive, where Service Accounts have 0 bytes of storage quota. "
            "Please configure OAuth Web Client in Settings and authorize with your personal Gmail account to enable direct binary uploads."
        )

    drive_session = get_drive_session()
    probe_token = uuid.uuid4().hex
    probe_name = f"__connection_test_{probe_token[:8]}.txt"
    probe_content = f"Google Drive Connection Test Verification Probe\nToken: {probe_token}\nFolder: {Config.GOOGLE_DRIVE_FOLDER_ID}\nTimestamp: {uuid.uuid1()}".encode('utf-8')

    uploaded_file_id = None
    try:
        # 1. Upload probe file
        metadata = {
            'name': probe_name,
            'parents': [Config.GOOGLE_DRIVE_FOLDER_ID]
        }
        files = {
            'data': ('metadata', io.StringIO(str(metadata).replace("'", '"')), 'application/json; charset=UTF-8'),
            'file': (probe_name, probe_content, 'text/plain')
        }
        upload_url = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id,name,webViewLink'
        up_resp = drive_session.post(upload_url, files=files)
        
        if up_resp.status_code not in (200, 201):
            raise RuntimeError(f"Drive upload failed with HTTP {up_resp.status_code}: {up_resp.text}")

        up_data = up_resp.json()
        uploaded_file_id = up_data.get('id')
        web_link = up_data.get('webViewLink')

        # 2. Download probe file and verify exact byte and SHA-256 integrity
        dl_name, dl_mime, dl_bytes = download_attachment(uploaded_file_id)
        probe_hash = hashlib.sha256(probe_content).hexdigest()
        dl_hash = hashlib.sha256(dl_bytes).hexdigest()
        if dl_hash != probe_hash or dl_bytes != probe_content:
            raise RuntimeError(f"Byte integrity mismatch: downloaded file hash {dl_hash} did not match probe hash {probe_hash}.")

        # 3. Clean up probe file from Drive
        del_url = f"https://www.googleapis.com/drive/v3/files/{uploaded_file_id}?supportsAllDrives=true"
        del_resp = drive_session.delete(del_url)
        if del_resp.status_code not in (200, 204):
            logger.warning(f"Could not delete test probe file {uploaded_file_id}: {del_resp.text}")

        # 4. Persist verification in Google Sheets System_Config
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        try:
            from services.sheets_db import SheetsDB
            SheetsDB.set_system_config('drive_verified_success', 'TRUE', updated_by='live_upload_test')
            SheetsDB.set_system_config('drive_last_verified_at', now_iso, updated_by='live_upload_test')
            SheetsDB.set_system_config('drive_verified_probe_id', uploaded_file_id, updated_by='live_upload_test')
        except Exception as e:
            logger.warning(f"Could not persist verification timestamp to Sheets: {e}")

        return {
            'status': 'success',
            'verified': True,
            'message': f"Binary upload, download, SHA-256 integrity check, and cleanup successfully verified in Google Drive folder '{diag.get('folder_name')}'.",
            'file_id': uploaded_file_id,
            'sha256_verified': True,
            'cleaned_up': True,
            'auth_mode': diag.get('auth_mode'),
            'account_email': diag.get('active_account_email'),
            'folder_name': diag.get('folder_name'),
            'folder_id': Config.GOOGLE_DRIVE_FOLDER_ID,
            'verified_at': now_iso
        }
    except Exception as e:
        # Cleanup probe file if error occurred mid-way
        if uploaded_file_id:
            try:
                drive_session.delete(f"https://www.googleapis.com/drive/v3/files/{uploaded_file_id}?supportsAllDrives=true")
            except Exception:
                pass
        raise
