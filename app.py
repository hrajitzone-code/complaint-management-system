import os
import io
import time
import json
import csv
import math
import secrets
import urllib.parse
import datetime
import logging
import threading
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from services.google_client import (
    get_service_account_info,
    check_drive_folder_access,
    get_google_credentials,
    get_drive_status_diagnostic,
    get_oauth_client_config
)
from services.sheets_db import SheetsDB
from services.mock_sheets_db import MockSheetsDB
from services.drive_storage import (
    upload_attachment,
    download_attachment,
    test_drive_upload_and_download
)
from services.auth_service import (
    get_current_user,
    login_required,
    staff_required,
    admin_required,
    super_admin_required,
    permission_required,
    record_access_required,
    verify_and_login_user,
    refresh_user_session
)
from services.rbac_service import (
    RBACService,
    ALL_PERMISSIONS,
    DEFAULT_ROLE_TEMPLATES,
    ALL_PERMISSION_IDS
)
from services.report_service import (
    ReportService,
    ALL_REPORT_COLUMNS,
    DEFAULT_COL_IDS,
    COLUMN_MAP
)
from services.excel_import_service import ExcelImportService

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('ComplaintApp')

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static'),
    static_url_path='/static'
)
app.config.from_object(Config)
app.permanent_session_lifetime = datetime.timedelta(hours=Config.PERMANENT_SESSION_LIFETIME_HOURS)

# Wrap with ProxyFix for reverse proxy trust (HTTPS headers, X-Forwarded-For, X-Forwarded-Proto, X-Forwarded-Host)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# In-memory thread-safe caches for debounce and rate limiting
_submission_lock = threading.Lock()
_recent_submissions = {}

_rate_limit_lock = threading.Lock()
_failed_login_attempts = {}

# Determine active database engine (Google Sheets vs Mock)
def get_db():
    if os.environ.get('TESTING') == 'True' or os.environ.get('USE_MOCK_STORAGE') == 'True':
        return MockSheetsDB
    # If credentials exist, use live SheetsDB
    if get_service_account_info():
        return SheetsDB
    raise RuntimeError(
        "Google Sheets credentials not configured. Please supply google_service_account.json "
        "or GOOGLE_SERVICE_ACCOUNT_JSON. Persistent database requires Google Sheets API."
    )

@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    return jsonify({
        'status': 'error',
        'message': f"Uploaded file is too large. Maximum allowed size is {Config.MAX_CONTENT_LENGTH // (1024*1024)}MB."
    }), 413

@app.route('/static/<path:filename>')
def serve_static_asset(filename):
    """Explicit static file handler for serverless and CDN fallback."""
    static_dir = os.path.join(BASE_DIR, 'static')
    return send_from_directory(static_dir, filename)

@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'API endpoint not found.'}), 404
    return render_template('index.html')

@app.errorhandler(500)
def handle_500(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({'status': 'error', 'message': 'Internal server error occurred. Please try again later.'}), 500

@app.before_request
def csrf_protect():
    """
    Blocks cross-site request forgery on state-changing authenticated requests.
    Validates browser Sec-Fetch-Site and Origin / Host headers.
    """
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        # Allow initial public authentication routes
        if request.path.startswith('/api/auth/'):
            return None

        # Check Sec-Fetch-Site header (sent automatically by modern browsers)
        sec_fetch_site = request.headers.get('Sec-Fetch-Site')
        if sec_fetch_site and sec_fetch_site == 'cross-site':
            logger.warning(f"CSRF attempt blocked by Sec-Fetch-Site: {sec_fetch_site} on {request.path}")
            return jsonify({'status': 'error', 'message': 'Cross-site request blocked.'}), 403

        # Check Origin header if present
        origin = request.headers.get('Origin')
        if origin:
            origin_host = origin.split('://', 1)[-1].rstrip('/')
            expected_host = request.host.rstrip('/')
            if origin_host != expected_host:
                logger.warning(f"CSRF attempt blocked by Origin mismatch: {origin_host} != {expected_host}")
                return jsonify({'status': 'error', 'message': 'Cross-site request blocked.'}), 403

# =============================================================================
# FRONTEND ENTRY POINT
# =============================================================================

@app.route('/')
def index():
    """Serves the Single-Page Application interface."""
    return render_template('index.html')

# =============================================================================
# SYSTEM & GOOGLE CONNECTION APIS
# =============================================================================

@app.route('/api/system/status', methods=['GET'])
def get_system_status():
    """Reports live Google Sheets & Drive API status."""
    sa_info = get_service_account_info()
    has_creds = bool(sa_info)
    sa_email = sa_info.get('client_email') if sa_info else None
    project_id = sa_info.get('project_id') if sa_info else None

    # Get comprehensive Drive diagnostic status
    drive_diag = get_drive_status_diagnostic(Config.GOOGLE_DRIVE_FOLDER_ID)
    redirect_uri = request.host_url.rstrip('/') + '/api/drive/oauth2callback'
    drive_diag['redirect_uri'] = redirect_uri

    mode = 'mock' if (os.environ.get('TESTING') == 'True' or not has_creds) else 'live'
    spreadsheet_id = None
    if mode == 'live':
        try:
            sh = SheetsDB.get_spreadsheet()
            spreadsheet_id = sh.id
        except Exception as e:
            logger.warning(f"Could not fetch spreadsheet ID during status check: {e}")

    return jsonify({
        'status': 'success',
        'mode': mode,
        'credentials_configured': has_creds,
        'service_account_email': sa_email,
        'project_id': project_id,
        'target_drive_folder_id': Config.GOOGLE_DRIVE_FOLDER_ID,
        'target_drive_folder_url': Config.GOOGLE_DRIVE_FOLDER_URL,
        'drive_folder_accessible': drive_diag.get('has_folder_access', False),
        'drive_folder_message': drive_diag.get('message', ''),
        'drive_auth_mode': drive_diag.get('auth_mode', 'service_account'),
        'drive_is_shared_drive': drive_diag.get('is_shared_drive', False),
        'can_upload_binary': drive_diag.get('can_upload_binary', False),
        'active_spreadsheet_id': spreadsheet_id,
        'lan_url': 'http://192.168.1.55:5050',
        'lan_ip': '192.168.1.55',
        'drive': drive_diag,
        'database_engine': 'Google Sheets API v4' if mode == 'live' else 'In-Memory Mock Store'
    })

# =============================================================================
# GOOGLE DRIVE & OAUTH 2.0 USER AUTHENTICATION APIS
# =============================================================================

def get_supported_redirect_uris():
    """
    Returns the list of supported redirect URIs for localhost, office LAN, and production Vercel.
    """
    uris = [
        "https://complaint-management-system-sigma-nine.vercel.app/api/drive/oauth2callback",
        "http://localhost:5050/api/drive/oauth2callback",
        "http://127.0.0.1:5050/api/drive/oauth2callback",
        "http://192.168.1.55:5050/api/drive/oauth2callback",
        "http://192.168.1.21:5050/api/drive/oauth2callback"
    ]
    vercel_url = os.environ.get('VERCEL_URL') or os.environ.get('NEXT_PUBLIC_VERCEL_URL')
    if vercel_url:
        clean_v = vercel_url.strip().rstrip('/')
        if not clean_v.startswith('http://') and not clean_v.startswith('https://'):
            clean_v = f"https://{clean_v}"
        vercel_uri = f"{clean_v}/api/drive/oauth2callback"
        if vercel_uri not in uris:
            uris.append(vercel_uri)
    custom_uri = os.environ.get('OAUTH_REDIRECT_URI')
    if custom_uri and custom_uri.strip() not in uris:
        uris.append(custom_uri.strip())
    return uris

def resolve_active_redirect_uri():
    """
    Resolves the best matching redirect URI based on the incoming request,
    supporting localhost, 127.0.0.1, or production Vercel with HTTPS.
    """
    if os.environ.get('OAUTH_REDIRECT_URI'):
        uri = os.environ.get('OAUTH_REDIRECT_URI').strip().rstrip('/')
        if not uri.endswith('/api/drive/oauth2callback'):
            uri = f"{uri}/api/drive/oauth2callback"
        return uri

    try:
        host = request.headers.get('X-Forwarded-Host') or request.host
        if host:
            if 'localhost' in host or '127.0.0.1' in host or '192.168.' in host:
                return f"http://{host}/api/drive/oauth2callback"
            return f"https://{host}/api/drive/oauth2callback"
    except Exception:
        pass

    if os.environ.get('VERCEL') == '1' or os.environ.get('VERCEL_URL'):
        v_url = os.environ.get('VERCEL_URL', '').strip().rstrip('/')
        if v_url:
            if not v_url.startswith('http'):
                v_url = f"https://{v_url}"
            return f"{v_url}/api/drive/oauth2callback"

    return "https://complaint-management-system-sigma-nine.vercel.app/api/drive/oauth2callback"

@app.route('/api/drive/status', methods=['GET'])
def get_drive_status_endpoint():
    """Reports detailed Drive connection status, quota, and OAuth configuration."""
    diag = get_drive_status_diagnostic(Config.GOOGLE_DRIVE_FOLDER_ID)
    active_uri = resolve_active_redirect_uri()
    diag['redirect_uri'] = active_uri
    diag['supported_redirect_uris'] = get_supported_redirect_uris()
    diag['oauth_setup_console_url'] = 'https://console.cloud.google.com/apis/credentials'
    diag['lan_url'] = 'http://192.168.1.55:5050'
    diag['lan_ip'] = '192.168.1.55'
    return jsonify({'status': 'success', 'drive': diag})

@app.route('/api/drive/configure-client', methods=['POST'])
@admin_required
def configure_oauth_client():
    """Allows Admin to configure or update Google OAuth Client ID and Secret."""
    data = request.get_json(silent=True) or {}
    client_id = (data.get('client_id') or '').strip()
    client_secret = (data.get('client_secret') or '').strip()

    if not client_id or not client_secret:
        return jsonify({'status': 'error', 'message': 'Both Client ID and Client Secret are required.'}), 400

    if not client_id.endswith('.apps.googleusercontent.com') and 'googleusercontent.com' not in client_id:
        return jsonify({'status': 'error', 'message': 'Invalid Google Client ID format. It should look like: [id].apps.googleusercontent.com'}), 400

    user = get_current_user()
    db = get_db()
    # Saves with encryption in System_Config (encrypted at rest using Vercel encryption key)
    db.set_system_config('google_user_client_id', client_id, updated_by=user['email'])
    db.set_system_config('google_user_client_secret', client_secret, updated_by=user['email'])

    return jsonify({
        'status': 'success',
        'message': 'Google OAuth Client credentials encrypted and saved to System_Config successfully.'
    })

@app.route('/api/drive/auth-url', methods=['GET'])
@admin_required
def get_drive_auth_url():
    """Generates the Google OAuth 2.0 consent URL for personal Gmail authorization."""
    client_id, client_secret = get_oauth_client_config()
    if not client_id or not client_secret:
        return jsonify({
            'status': 'error',
            'message': 'Google OAuth Client ID & Secret are not configured yet. Please configure them in Settings.'
        }), 400

    req_uri = request.args.get('redirect_uri')
    supported = get_supported_redirect_uris()
    if req_uri and req_uri.strip() in supported:
        redirect_uri = req_uri.strip()
    else:
        redirect_uri = resolve_active_redirect_uri()

    state_token = secrets.token_urlsafe(16)
    session['oauth_state'] = state_token
    session['oauth_redirect_uri'] = redirect_uri

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/drive',
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true',
        'login_hint': 'hr.ajitzone@gmail.com',
        'state': state_token
    }
    auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)
    return jsonify({
        'status': 'success',
        'auth_url': auth_url,
        'redirect_uri': redirect_uri,
        'supported_redirect_uris': supported
    })

@app.route('/api/drive/oauth2callback', methods=['GET'])
def drive_oauth2_callback():
    """Callback endpoint invoked by Google OAuth consent flow."""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        return redirect(f"/#settings?drive_auth=error&msg={urllib.parse.quote(error)}")
    if not code:
        return redirect("/#settings?drive_auth=error&msg=missing_code")

    client_id, client_secret = get_oauth_client_config()
    if not client_id or not client_secret:
        return redirect("/#settings?drive_auth=error&msg=missing_client_config")

    redirect_uri = session.get('oauth_redirect_uri') or resolve_active_redirect_uri()

    try:
        token_resp = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'code': code,
                'client_id': client_id,
                'client_secret': client_secret,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code'
            },
            timeout=15
        )
        if token_resp.status_code != 200:
            err_msg = token_resp.text
            logger.error(f"OAuth token exchange error: {err_msg}")
            return redirect(f"/#settings?drive_auth=error&msg={urllib.parse.quote('Token exchange error: ' + err_msg[:120])}")

        token_data = token_resp.json()
        refresh_token = token_data.get('refresh_token')
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 3600)

        db = get_db()
        # Stored encrypted in System_Config via SheetsDB
        # CRITICAL RULE: Never overwrite a valid refresh token with a missing, None, or empty token!
        if refresh_token and str(refresh_token).strip():
            db.set_system_config('google_user_refresh_token', str(refresh_token).strip(), updated_by='oauth_callback')
            logger.info("New Google Drive refresh token saved to System_Config successfully.")
        else:
            logger.warning("No new refresh token in Google OAuth response; existing refresh token retained.")

        if access_token and str(access_token).strip():
            db.set_system_config('google_user_access_token', str(access_token).strip(), updated_by='oauth_callback')
            try:
                expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=int(expires_in))
                db.set_system_config('google_user_token_expiry', expiry_dt.strftime('%Y-%m-%d %H:%M:%S'), updated_by='oauth_callback')
            except Exception as exp_err:
                logger.debug(f"Could not calculate/save token expiry: {exp_err}")

        # Identify authenticated user email
        user_email = ''
        try:
            about_resp = requests.get(
                'https://www.googleapis.com/drive/v3/about?fields=user',
                headers={'Authorization': f"Bearer {access_token}"},
                timeout=10
            )
            if about_resp.status_code == 200:
                user_email = about_resp.json().get('user', {}).get('emailAddress', '')
                if user_email:
                    db.set_system_config('google_user_email', user_email, updated_by='oauth_callback')
        except Exception as e:
            logger.warning(f"Could not retrieve user email during callback: {e}")

        logger.info(f"Google Drive User OAuth tokens saved for account: {user_email}")

        # Automatically execute live upload, download, SHA-256 integrity, and cleanup test
        verify_success = False
        verify_error = ''
        try:
            from services.drive_storage import test_drive_upload_and_download
            test_res = test_drive_upload_and_download()
            verify_success = test_res.get('verified', False)
            logger.info(f"Drive OAuth live probe successfully verified: {test_res.get('file_id')}")
        except Exception as test_err:
            verify_error = str(test_err)
            logger.warning(f"Drive OAuth token received, but automated live probe failed: {verify_error}")

        if verify_success:
            return redirect(f"/#settings?drive_auth=success&account={urllib.parse.quote(user_email or 'User')}&verified=true")
        else:
            return redirect(f"/#settings?drive_auth=authorized&account={urllib.parse.quote(user_email or 'User')}&verified=false&verify_err={urllib.parse.quote(verify_error[:120])}")
    except Exception as e:
        logger.error(f"Exception during OAuth callback: {e}")
        return redirect(f"/#settings?drive_auth=error&msg={urllib.parse.quote(str(e))}")

@app.route('/api/drive/exchange-code', methods=['POST'])
@admin_required
def exchange_oauth_code_endpoint():
    """
    Allows Admin/Super Admin to manually exchange an authorization code or tokens,
    persisting them securely with encryption to System_Config and running the live probe test.
    """
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip()
    direct_refresh_token = (data.get('refresh_token') or '').strip()
    redirect_uri = (data.get('redirect_uri') or '').strip() or resolve_active_redirect_uri()

    client_id, client_secret = get_oauth_client_config()
    if not client_id or not client_secret:
        return jsonify({'status': 'error', 'message': 'Client ID and Client Secret not configured.'}), 400

    db = get_db()
    current_user = get_current_user()
    updater = current_user.get('email', 'admin')

    user_email = ''
    if code:
        try:
            token_resp = requests.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'code': code,
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'redirect_uri': redirect_uri,
                    'grant_type': 'authorization_code'
                },
                timeout=15
            )
            if token_resp.status_code != 200:
                return jsonify({
                    'status': 'error',
                    'message': f"Token exchange failed (HTTP {token_resp.status_code}): {token_resp.text}"
                }), 400

            token_data = token_resp.json()
            refresh_token = token_data.get('refresh_token')
            access_token = token_data.get('access_token')
            expires_in = token_data.get('expires_in', 3600)

            if refresh_token and str(refresh_token).strip():
                db.set_system_config('google_user_refresh_token', str(refresh_token).strip(), updated_by=updater)
            if access_token and str(access_token).strip():
                db.set_system_config('google_user_access_token', str(access_token).strip(), updated_by=updater)
                expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=int(expires_in))
                db.set_system_config('google_user_token_expiry', expiry_dt.strftime('%Y-%m-%d %H:%M:%S'), updated_by=updater)

            if access_token:
                try:
                    about_resp = requests.get(
                        'https://www.googleapis.com/drive/v3/about?fields=user',
                        headers={'Authorization': f"Bearer {access_token}"},
                        timeout=10
                    )
                    if about_resp.status_code == 200:
                        user_email = about_resp.json().get('user', {}).get('emailAddress', '')
                        if user_email:
                            db.set_system_config('google_user_email', user_email, updated_by=updater)
                except Exception:
                    pass

        except Exception as e:
            return jsonify({'status': 'error', 'message': f"Error exchanging code: {str(e)}"}), 500

    elif direct_refresh_token:
        db.set_system_config('google_user_refresh_token', direct_refresh_token, updated_by=updater)
    else:
        return jsonify({'status': 'error', 'message': 'Either code or refresh_token is required.'}), 400

    # Run verification test
    try:
        from services.drive_storage import test_drive_upload_and_download
        test_res = test_drive_upload_and_download()
        return jsonify({
            'status': 'success',
            'message': 'Token exchanged and live upload/download verified successfully.',
            'verification': test_res
        })
    except Exception as e:
        return jsonify({
            'status': 'partial_success',
            'message': f"Tokens saved, but live test failed: {str(e)}",
            'error': str(e)
        }), 200

@app.route('/api/drive/test-upload', methods=['POST'])
@admin_required
def test_drive_upload_endpoint():
    """Runs a live binary test: uploads a test file, downloads it, verifies byte integrity, and deletes it."""
    try:
        result = test_drive_upload_and_download()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Live Drive test upload failed: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/drive/disconnect', methods=['POST'])
@admin_required
def disconnect_drive_oauth():
    """Disconnects Google Drive User OAuth and clears saved tokens."""
    try:
        user = get_current_user()
        db = get_db()
        db.delete_system_config('google_user_refresh_token')
        db.delete_system_config('google_user_access_token')
        db.delete_system_config('google_user_email')
        db.delete_system_config('drive_verified_success')
        db.delete_system_config('drive_last_verified_at')
        db.delete_system_config('drive_verified_probe_id')
        return jsonify({
            'status': 'success',
            'message': 'Google Drive User OAuth disconnected. System reverted to default Service Account.'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/drive/download/<file_id>', methods=['GET'])
@login_required
@permission_required('attachments:view')
def download_drive_file(file_id):
    """Streams an attachment file from Google Drive directly to the user."""
    try:
        filename, mime_type, file_bytes = download_attachment(file_id)
        return send_file(
            io.BytesIO(file_bytes),
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Download failed for file {file_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Could not download file: {str(e)}"}), 404

@app.route('/api/drive/view/<file_id>', methods=['GET'])
@login_required
@permission_required('attachments:view')
def view_drive_file(file_id):
    """Redirects to the Google Drive preview link for the file."""
    return redirect(f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk")

@app.route('/api/system/setup-status', methods=['GET'])
def get_setup_status():
    """Reports whether initial admin provisioning is required."""
    try:
        db = get_db()
        has_admin = db.has_admin_user()
        return jsonify({
            'status': 'success',
            'needs_admin_setup': not has_admin,
            'has_admin': has_admin
        })
    except Exception as e:
        logger.error(f"Error checking setup status: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =============================================================================
# AUTHENTICATION APIS
# =============================================================================

@app.route('/api/auth/setup-admin', methods=['POST'])
def setup_initial_admin():
    """Provisions the initial Super Administrator account when none exists."""
    try:
        db = get_db()
        if db.has_admin_user():
            return jsonify({
                'status': 'error',
                'message': 'Initial administrator has already been provisioned. Setup is locked.'
            }), 403

        data = request.get_json(silent=True) or request.form or {}
        full_name = (data.get('full_name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        password = (data.get('password') or '').strip()

        if not full_name or not email or not password:
            return jsonify({'status': 'error', 'message': 'Full name, email, and password are required.'}), 400

        if len(password) < 8:
            return jsonify({'status': 'error', 'message': 'Admin password must be at least 8 characters long.'}), 400

        user = db.create_initial_admin(
            full_name=full_name,
            email=email,
            password=password
        )

        # Automatically log in the provisioned admin
        session.clear()
        session['user_id'] = user.get('User ID')
        session['user_email'] = user.get('Email')
        session['user_name'] = user.get('Full Name')
        session['user_role'] = user.get('Role')
        session['user_dept'] = user.get('Department')
        session.permanent = True

        return jsonify({
            'status': 'success',
            'message': 'Super Administrator provisioned successfully! You are now logged in.',
            'user': {
                'id': user.get('User ID'),
                'name': user.get('Full Name'),
                'email': user.get('Email'),
                'role': user.get('Role'),
                'department': user.get('Department')
            }
        }), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error provisioning initial admin: {e}")
        return jsonify({'status': 'error', 'message': f"Setup failed: {str(e)}"}), 500

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Authenticates email and password against Google Sheets Users database with brute-force rate limiting."""
    data = request.get_json(silent=True) or request.form or {}
    email = (data.get('email') or data.get('username') or '').strip().lower()
    password = data.get('password')

    client_ip = request.remote_addr or 'unknown'
    rate_key = f"{client_ip}:{email}"
    now_ts = time.time()

    # Enforce brute-force rate limit (max 5 failed attempts per 60 seconds per IP/email)
    with _rate_limit_lock:
        recent_failures = [t for t in _failed_login_attempts.get(rate_key, []) if now_ts - t < 60]
        _failed_login_attempts[rate_key] = recent_failures
        if len(recent_failures) >= 5:
            logger.warning(f"Login rate limit exceeded for key: {rate_key}")
            return jsonify({
                'status': 'error',
                'message': 'Too many failed login attempts. Please wait 60 seconds before trying again.'
            }), 429

    db = get_db()
    try:
        success, user_data, err_msg = verify_and_login_user(email, password, db)
    except Exception as e:
        logger.error(f"Database error during login: {e}")
        sa_info = get_service_account_info()
        sa_email = sa_info.get('client_email') if sa_info else 'Service Account'
        return jsonify({
            'status': 'error',
            'message': f"Google Sheets access error: {str(e)}. Please ensure spreadsheet '{Config.COMPLAINTS_SPREADSHEET_ID}' is shared with '{sa_email}' as Editor in Google Drive."
        }), 503

    if not success:
        with _rate_limit_lock:
            _failed_login_attempts.setdefault(rate_key, []).append(time.time())
        return jsonify({'status': 'error', 'message': err_msg}), 401

    # Clear failure history on successful authentication
    with _rate_limit_lock:
        _failed_login_attempts.pop(rate_key, None)

    # Log successful login in audit log
    try:
        db.log_audit_event(
            user_id=user_data.get('user_id'),
            user_email=user_data.get('email'),
            user_role=user_data.get('role'),
            action='LOGIN',
            module='AUTH',
            record_id=user_data.get('user_id'),
            department=user_data.get('department'),
            details='User logged in successfully.',
            result='SUCCESS',
            ip_address=client_ip
        )
    except Exception as log_err:
        logger.warning(f"Could not log login audit: {log_err}")

    return jsonify({
        'status': 'success',
        'message': f"Welcome back, {user_data['name']}!",
        'user': user_data
    })

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    """Allows new users / complainants to register an account."""
    data = request.get_json(silent=True) or request.form or {}
    full_name = (data.get('full_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()
    department = (data.get('department') or 'General').strip()

    if not full_name or not email or not password:
        return jsonify({'status': 'error', 'message': 'Full name, email, and password are required.'}), 400

    if len(password) < 6:
        return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters long.'}), 400

    db = get_db()
    try:
        new_user = db.create_user(
            full_name=full_name,
            email=email,
            password=password,
            role='User',
            department=department
        )
        return jsonify({
            'status': 'success',
            'message': 'Account registered successfully! You can now log in.',
            'user': new_user
        }), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error registering user: {e}")
        return jsonify({'status': 'error', 'message': f"Registration failed: {str(e)}"}), 500

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Terminates session and records audit event."""
    u = get_current_user()
    if u:
        try:
            db = get_db()
            db.log_audit_event(
                user_id=u.get('user_id'),
                user_email=u.get('email'),
                user_role=u.get('role'),
                action='LOGOUT',
                module='AUTH',
                record_id=u.get('user_id'),
                department=u.get('department'),
                details='User logged out.',
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as e:
            logger.warning(f"Could not log logout audit: {e}")
    session.clear()
    return jsonify({'status': 'success', 'message': 'Logged out successfully.'})

@app.route('/api/auth/refresh_permissions', methods=['POST'])
@login_required
def auth_refresh_permissions():
    """Refreshes current user's session permissions and departments from the database."""
    u = get_current_user()
    db = get_db()
    try:
        updated_user = refresh_user_session(u['email'], db)
        return jsonify({
            'status': 'success',
            'message': 'Permissions synchronized.',
            'user': updated_user
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    """Returns currently authenticated user with effective permissions and departments."""
    u = get_current_user()
    if not u:
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401

    # Live refresh against Google Sheets Users database to prevent stale permissions / deactivated accounts
    db = get_db()
    try:
        user_identifier = u.get('user_id') or u.get('email')
        updated_user = refresh_user_session(user_identifier, db)
        if not updated_user:
            session.clear()
            return jsonify({'status': 'error', 'message': 'Account is inactive, archived, or removed.'}), 403
        u = updated_user
    except Exception as e:
        logger.warning(f"Could not live refresh user session in /api/auth/me: {e}")
        if str(u.get('is_active', 'TRUE')).upper() not in ('TRUE', '1', 'YES'):
            session.clear()
            return jsonify({'status': 'error', 'message': 'Account is inactive or archived.'}), 403

    return jsonify({'status': 'success', 'user': u})

# =============================================================================
# DEPARTMENTS APIS
# =============================================================================

@app.route('/api/departments', methods=['GET'])
def list_departments():
    """Lists departments. Supports ?all=true for admin to view inactive ones as well."""
    db = get_db()
    include_inactive = request.args.get('all', '').lower() in ('true', '1')
    try:
        depts = db.get_all_departments(include_inactive=include_inactive)
        return jsonify({'status': 'success', 'departments': depts})
    except Exception as e:
        logger.error(f"Error reading departments: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to fetch departments: {str(e)}"}), 500

@app.route('/api/departments/<dept_id>', methods=['GET'])
def get_department_endpoint(dept_id):
    """Retrieves single department details."""
    db = get_db()
    try:
        dept = db.get_department_by_id(dept_id)
        if not dept:
            return jsonify({'status': 'error', 'message': f"Department '{dept_id}' not found."}), 404
        return jsonify({'status': 'success', 'department': dept})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/departments/<dept_id>/usage', methods=['GET'])
@admin_required
def get_department_usage_endpoint(dept_id):
    """Admin checks how many complaints are linked to this department before deletion."""
    db = get_db()
    try:
        dept, count, linked_ids = db.get_department_usage(dept_id)
        return jsonify({
            'status': 'success',
            'department': dept,
            'department_name': dept.get('Name', ''),
            'usage_count': count,
            'linked_complaint_ids': linked_ids,
            'complaint_ids': linked_ids,
            'can_delete_without_reassign': (count == 0)
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/departments', methods=['POST'])
@login_required
@permission_required('departments:create')
def create_department():
    """Admin creates a new complaint department."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('role_name') or '').strip()
    description = (data.get('description') or '').strip()
    lead_email = (data.get('lead_email') or '').strip()

    if not name:
        return jsonify({'status': 'error', 'message': 'Department name is required.'}), 400

    db = get_db()
    try:
        dept = db.create_department(name=name, description=description, lead_email=lead_email)
        try:
            current_user = get_current_user() or {}
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action='CREATE',
                module='Departments',
                record_id=dept.get('Department ID') if isinstance(dept, dict) else '',
                department=name,
                details=f"Created department '{name}'.",
                new_state=json.dumps(dept) if isinstance(dept, dict) else '',
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not log department audit event: {audit_err}")
        return jsonify({'status': 'success', 'message': f"Department '{name}' created.", 'department': dept}), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error creating department: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to create department: {str(e)}"}), 500

@app.route('/api/departments/<dept_id>', methods=['PUT'])
@login_required
@permission_required('departments:edit')
def update_department_endpoint(dept_id):
    """Admin updates department details."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('role_name') or '').strip()
    description = (data.get('description') or '').strip()
    lead_email = (data.get('lead_email') or '').strip()
    is_active = data.get('is_active', 'TRUE')

    if not name:
        return jsonify({'status': 'error', 'message': 'Department name is required.'}), 400

    db = get_db()
    try:
        updated = db.update_department(
            dept_id=dept_id,
            name=name,
            description=description,
            lead_email=lead_email,
            is_active=is_active
        )
        return jsonify({
            'status': 'success',
            'message': f"Department '{name}' updated successfully.",
            'department': updated
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error updating department {dept_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to update department: {str(e)}"}), 500

@app.route('/api/departments/<dept_id>', methods=['DELETE'])
@app.route('/api/departments/<dept_id>/delete', methods=['POST'])
@login_required
@permission_required('departments:delete')
def delete_department_endpoint(dept_id):
    """
    Admin deletes a department.
    Prevents deletion if linked to complaints unless a safe replacement department is specified.
    """
    data = request.get_json(silent=True) or request.form or {}
    reassign_to = request.args.get('reassign_to') or data.get('reassign_to')

    db = get_db()
    try:
        db.delete_department(dept_id=dept_id, reassign_to_dept_id_or_name=reassign_to)
        return jsonify({
            'status': 'success',
            'message': f"Department {dept_id} deleted successfully."
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 409
    except Exception as e:
        logger.error(f"Error deleting department {dept_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete department: {str(e)}"}), 500

# =============================================================================
# MASTER DATA MANAGEMENT APIS (Complaint Types, Salespersons, Options)
# =============================================================================

@app.route('/api/master/options', methods=['GET'])
def get_master_options():
    """
    Public / logged-in endpoint returning active master-data options for form dropdowns:
    - Active Complaint Types
    - Active Salespersons
    - Active Departments
    - Canonical From Department choices (['Online', 'Offline', 'Packing', 'None'])
    """
    db = get_db()
    try:
        types = db.get_all_complaint_types(include_inactive=False)
        salespersons = db.get_all_salespersons(include_inactive=False)
        departments = db.get_all_departments(include_inactive=False)
        return jsonify({
            'status': 'success',
            'complaint_types': [t['Name'] for t in types if t.get('Name')],
            'complaint_types_detailed': types,
            'salespersons': [s['Name'] for s in salespersons if s.get('Name')],
            'salespersons_detailed': salespersons,
            'departments': [d['Name'] for d in departments if d.get('Name')],
            'from_departments': ['Online', 'Offline', 'Packing', 'None']
        })
    except Exception as e:
        logger.error(f"Error loading master options: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to load master options: {str(e)}"}), 500

# --- Complaint Types ---

@app.route('/api/master/complaint-types', methods=['GET'])
def list_complaint_types():
    """Lists complaint types. Supports ?all=true for admin to inspect inactive items."""
    db = get_db()
    include_inactive = request.args.get('all', '').lower() in ('true', '1')
    try:
        types = db.get_all_complaint_types(include_inactive=include_inactive)
        return jsonify({'status': 'success', 'complaint_types': types})
    except Exception as e:
        logger.error(f"Error fetching complaint types: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to fetch complaint types: {str(e)}"}), 500

@app.route('/api/master/complaint-types/<type_id>', methods=['GET'])
def get_complaint_type(type_id):
    """Retrieves single complaint type details."""
    db = get_db()
    try:
        ct = db.get_complaint_type_by_id(type_id)
        if not ct:
            return jsonify({'status': 'error', 'message': f"Complaint Type '{type_id}' not found."}), 404
        return jsonify({'status': 'success', 'complaint_type': ct})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/master/complaint-types/<type_id>/usage', methods=['GET'])
@login_required
@permission_required('master_data:manage')
def get_complaint_type_usage_endpoint(type_id):
    """Checks complaint type usage in complaints before deletion."""
    db = get_db()
    try:
        ct, count, linked_ids = db.get_complaint_type_usage(type_id)
        return jsonify({
            'status': 'success',
            'complaint_type': ct,
            'name': ct.get('Name', ''),
            'usage_count': count,
            'linked_complaint_ids': linked_ids,
            'can_delete': (count == 0)
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/master/complaint-types', methods=['POST'])
@login_required
@permission_required('master_data:manage')
def create_complaint_type_endpoint():
    """Admin creates a new complaint type."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('role_name') or '').strip()
    description = (data.get('description') or '').strip()

    if not name:
        return jsonify({'status': 'error', 'message': 'Complaint type name is required.'}), 400

    db = get_db()
    try:
        ct = db.create_complaint_type(name=name, description=description)
        return jsonify({'status': 'success', 'message': f"Complaint Type '{name}' created.", 'complaint_type': ct}), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error creating complaint type: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to create complaint type: {str(e)}"}), 500

@app.route('/api/master/complaint-types/<type_id>', methods=['PUT'])
@login_required
@permission_required('master_data:manage')
def update_complaint_type_endpoint(type_id):
    """Admin updates complaint type details or toggles active status."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('role_name') or '').strip()
    description = (data.get('description') or '').strip()
    is_active = data.get('is_active', 'TRUE')

    if not name:
        return jsonify({'status': 'error', 'message': 'Complaint type name is required.'}), 400

    db = get_db()
    try:
        updated = db.update_complaint_type(type_id=type_id, name=name, description=description, is_active=is_active)
        return jsonify({
            'status': 'success',
            'message': f"Complaint Type '{name}' updated successfully.",
            'complaint_type': updated
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error updating complaint type {type_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to update complaint type: {str(e)}"}), 500

@app.route('/api/master/complaint-types/<type_id>', methods=['DELETE'])
@app.route('/api/master/complaint-types/<type_id>/delete', methods=['POST'])
@login_required
@permission_required('master_data:manage')
def delete_complaint_type_endpoint(type_id):
    """
    Admin deletes a complaint type.
    Blocks deletion if referenced by any complaints; instructs admin to deactivate instead.
    """
    db = get_db()
    try:
        db.delete_complaint_type(type_id=type_id)
        return jsonify({'status': 'success', 'message': f"Complaint Type {type_id} deleted successfully."})
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 409
    except Exception as e:
        logger.error(f"Error deleting complaint type {type_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete complaint type: {str(e)}"}), 500

# --- Salespersons ---

@app.route('/api/master/salespersons', methods=['GET'])
def list_salespersons():
    """Lists salespersons. Supports ?all=true for admin to inspect inactive ones."""
    db = get_db()
    include_inactive = request.args.get('all', '').lower() in ('true', '1')
    try:
        sp_list = db.get_all_salespersons(include_inactive=include_inactive)
        return jsonify({'status': 'success', 'salespersons': sp_list})
    except Exception as e:
        logger.error(f"Error fetching salespersons: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to fetch salespersons: {str(e)}"}), 500

@app.route('/api/master/salespersons/<sp_id>', methods=['GET'])
def get_salesperson(sp_id):
    """Retrieves single salesperson details."""
    db = get_db()
    try:
        sp = db.get_salesperson_by_id(sp_id)
        if not sp:
            return jsonify({'status': 'error', 'message': f"Salesperson '{sp_id}' not found."}), 404
        return jsonify({'status': 'success', 'salesperson': sp})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/master/salespersons/<sp_id>/usage', methods=['GET'])
@login_required
@permission_required('master_data:manage')
def get_salesperson_usage_endpoint(sp_id):
    """Checks salesperson usage in complaints before deletion."""
    db = get_db()
    try:
        sp, count, linked_ids = db.get_salesperson_usage(sp_id)
        return jsonify({
            'status': 'success',
            'salesperson': sp,
            'name': sp.get('Name', ''),
            'usage_count': count,
            'linked_complaint_ids': linked_ids,
            'can_delete': (count == 0)
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/master/salespersons', methods=['POST'])
@login_required
@permission_required('master_data:manage')
def create_salesperson_endpoint():
    """Admin creates a new salesperson."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('role_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    email = (data.get('email') or '').strip()

    if not name:
        return jsonify({'status': 'error', 'message': 'Salesperson name is required.'}), 400

    db = get_db()
    try:
        sp = db.create_salesperson(name=name, phone=phone, email=email)
        return jsonify({'status': 'success', 'message': f"Salesperson '{name}' created.", 'salesperson': sp}), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error creating salesperson: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to create salesperson: {str(e)}"}), 500

@app.route('/api/master/salespersons/<sp_id>', methods=['PUT'])
@login_required
@permission_required('master_data:manage')
def update_salesperson_endpoint(sp_id):
    """Admin updates salesperson details or toggles active status."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('role_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    email = (data.get('email') or '').strip()
    is_active = data.get('is_active', 'TRUE')

    if not name:
        return jsonify({'status': 'error', 'message': 'Salesperson name is required.'}), 400

    db = get_db()
    try:
        updated = db.update_salesperson(sp_id=sp_id, name=name, phone=phone, email=email, is_active=is_active)
        return jsonify({
            'status': 'success',
            'message': f"Salesperson '{name}' updated successfully.",
            'salesperson': updated
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error updating salesperson {sp_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to update salesperson: {str(e)}"}), 500

@app.route('/api/master/salespersons/<sp_id>', methods=['DELETE'])
@app.route('/api/master/salespersons/<sp_id>/delete', methods=['POST'])
@login_required
@permission_required('master_data:manage')
def delete_salesperson_endpoint(sp_id):
    """
    Admin deletes a salesperson.
    Blocks deletion if referenced by any complaints; instructs admin to deactivate instead.
    """
    db = get_db()
    try:
        db.delete_salesperson(sp_id=sp_id)
        return jsonify({'status': 'success', 'message': f"Salesperson {sp_id} deleted successfully."})
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 409
    except Exception as e:
        logger.error(f"Error deleting salesperson {sp_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete salesperson: {str(e)}"}), 500

# =============================================================================
# COMPLAINTS APIS
# =============================================================================

# =============================================================================
# EXCEL BULK IMPORT & TEMPLATE DOWNLOAD APIS
# =============================================================================

@app.route('/api/complaints/import/template', methods=['GET'])
@login_required
@permission_required('complaints:create')
def download_excel_template():
    """
    Generates and downloads a pre-formatted Excel template for bulk complaint uploads.
    Includes canonical columns, sample data, and dropdown data validation.
    """
    try:
        user = get_current_user()
        db = get_db()
        auth_depts = RBACService.get_user_authorized_departments(user)
        output_stream = ExcelImportService.generate_template(db, authorized_departments=auth_depts)
        
        return send_file(
            output_stream,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='complaint_import_template.xlsx'
        )
    except Exception as e:
        logger.error(f"Error generating Excel template: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to generate Excel template: {str(e)}"}), 500


@app.route('/api/complaints/import/preview', methods=['POST'])
@login_required
@permission_required('complaints:create')
def preview_excel_import():
    """
    Receives an uploaded Excel file, validates every row against mandatory fields,
    master data, department RBAC scoping, and duplicate detection.
    Returns preview summary counts and valid/invalid rows.
    """
    try:
        user = get_current_user()
        db = get_db()

        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file uploaded. Please upload a valid Excel file (.xlsx or .xls).'}), 400

        file_obj = request.files['file']
        filename = (file_obj.filename or '').strip()
        if not filename or not (filename.lower().endswith('.xlsx') or filename.lower().endswith('.xls')):
            return jsonify({'status': 'error', 'message': 'Invalid file format. Please upload an Excel (.xlsx or .xls) file.'}), 400

        result = ExcelImportService.parse_and_validate(file_obj.stream, current_user=user, db_engine=db)

        return jsonify({
            'status': 'success',
            'data': result
        })
    except ValueError as ve:
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error validating Excel import: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to parse and validate Excel file: {str(e)}"}), 500


@app.route('/api/complaints/import/master-options', methods=['GET'])
@login_required
@permission_required('complaints:create')
def get_import_master_options():
    """
    Returns active company departments, complaint types, salespersons,
    from-departments, priorities, and statuses for frontend dropdowns and datalists.
    """
    try:
        db = get_db()
        options = ExcelImportService.get_master_options(db)
        return jsonify({
            'status': 'success',
            'data': options
        })
    except Exception as e:
        logger.error(f"Error fetching import master options: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to fetch master options: {str(e)}"}), 500


@app.route('/api/complaints/import/revalidate', methods=['POST'])
@login_required
@permission_required('complaints:create')
def revalidate_excel_import():
    """
    Revalidates modified rows from the frontend editable preview table.
    Updates preview cache and returns recomputed valid/invalid rows and counts.
    """
    try:
        user = get_current_user()
        db = get_db()
        data = request.get_json(silent=True) or {}
        preview_token = data.get('preview_token')
        rows = data.get('rows', [])

        if not preview_token:
            return jsonify({'status': 'error', 'message': 'Preview token is required.'}), 400

        result = ExcelImportService.revalidate_batch(preview_token, rows, current_user=user, db_engine=db)

        return jsonify({
            'status': 'success',
            'data': result
        })
    except PermissionError as pe:
        return jsonify({'status': 'error', 'message': str(pe)}), 403
    except ValueError as ve:
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error revalidating Excel import rows: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to revalidate rows: {str(e)}"}), 500


@app.route('/api/complaints/import/commit', methods=['POST'])
@login_required
@permission_required('complaints:create')
def commit_excel_import():
    """
    Imports the validated complaint rows into Google Sheets Complaints & Status History.
    Verifies user preview session, executes batch append, and records audit trail.
    Supports importing valid rows while retaining remaining invalid rows in the preview cache.
    """
    try:
        user = get_current_user()
        db = get_db()

        data = request.get_json(silent=True) or {}
        preview_token = data.get('preview_token')
        if not preview_token:
            return jsonify({'status': 'error', 'message': 'Preview token is required.'}), 400

        # Optional rows payload if modified in frontend before commit
        rows = data.get('rows')
        if rows is not None:
            ExcelImportService.revalidate_batch(preview_token, rows, current_user=user, db_engine=db)

        preview_data = ExcelImportService.get_cached_preview(preview_token)
        if not preview_data:
            return jsonify({
                'status': 'error',
                'message': 'Preview session expired or not found. Please re-upload your Excel file.'
            }), 400

        # Verify session ownership
        if str(preview_data.get('user_id', '')) != str(user.get('user_id', '')):
            return jsonify({'status': 'error', 'message': 'Unauthorized preview session.'}), 403

        valid_rows = preview_data.get('valid_rows', [])
        if not valid_rows:
            return jsonify({'status': 'error', 'message': 'No valid records found to import.'}), 400

        # Bulk write to Google Sheets Complaints & Status History
        created_records = db.bulk_create_complaints(valid_rows, user)

        # Retain invalid rows in cache if user chose to import valid only
        import_valid_only = data.get('import_valid_only', True)
        remaining_invalid = preview_data.get('invalid_rows', [])
        if import_valid_only:
            preview_data['valid_rows'] = []

        # Audit logging
        try:
            db.log_audit_event(
                user_id=user.get('user_id'),
                user_email=user.get('email'),
                user_role=user.get('role'),
                action='EXCEL_IMPORT',
                module='COMPLAINTS',
                record_id=f"BATCH-{len(created_records)}",
                department=user.get('department'),
                details=f"Bulk imported {len(created_records)} complaint(s) via Excel upload ({len(remaining_invalid)} remaining invalid).",
                result='SUCCESS',
                ip_address=request.remote_addr
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log for excel import: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"Successfully imported {len(created_records)} complaint(s).",
            'imported_count': len(created_records),
            'failed_count': len(remaining_invalid),
            'remaining_invalid': remaining_invalid
        })
    except Exception as e:
        logger.error(f"Error committing Excel import: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to import complaints: {str(e)}"}), 500


@app.route('/api/complaints/import/error-report', methods=['POST', 'GET'])
@login_required
@permission_required('complaints:create')
def download_error_report():
    """
    Downloads rejected Excel rows with highlighted error descriptions.
    Supports preview_token via query param or POST body, or direct invalid_rows payload.
    """
    try:
        user = get_current_user()
        preview_token = request.args.get('preview_token')
        invalid_rows = []

        if not preview_token and request.is_json:
            body = request.get_json(silent=True) or {}
            preview_token = body.get('preview_token')
            invalid_rows = body.get('invalid_rows', [])

        if preview_token:
            preview_data = ExcelImportService.get_cached_preview(preview_token)
            if preview_data:
                invalid_rows = preview_data.get('invalid_rows', [])

        if not invalid_rows:
            return jsonify({'status': 'error', 'message': 'No rejected rows found to generate error report.'}), 400

        stream = ExcelImportService.generate_error_report(invalid_rows)
        return send_file(
            stream,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='rejected_complaints_error_report.xlsx'
        )
    except Exception as e:
        logger.error(f"Error generating error report: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to generate error report: {str(e)}"}), 500


@app.route('/api/complaints', methods=['POST'])
@login_required
@permission_required('complaints:create')
def lodge_complaint():
    """
    Lodges a new complaint with all 21 fields, unique ID, timestamp, and optional attachment.
    Writes directly to Google Sheets Complaints & Status History worksheets.
    """
    user = get_current_user()
    
    # Handle both multipart/form-data (with file), urlencoded form, and application/json
    if request.is_json:
        raw_data = request.get_json(silent=True) or {}
        file_obj = None
    else:
        raw_data = request.form.to_dict()
        file_obj = request.files.get('attachment')

    # Extract all 21 fields (support canonical & snake_case/legacy aliases)
    employee_name = (raw_data.get('employee_name') or raw_data.get('Employee Name') or user['name']).strip()
    complain_date = (raw_data.get('complain_date') or raw_data.get('Complain Date') or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')).strip()
    customer_name = (raw_data.get('customer_name') or raw_data.get('Customer Name') or raw_data.get('complainant_name') or user['name']).strip()
    invoice_no = (raw_data.get('invoice_no') or raw_data.get('Invoice No.') or '').strip()
    complain_types = (raw_data.get('complain_types') or raw_data.get('Complain Types') or raw_data.get('subject') or '').strip()
    priority = (raw_data.get('priority') or raw_data.get('Priority') or 'Medium').strip().capitalize()
    remarks = (raw_data.get('remarks') or raw_data.get('Remarks') or '').strip()
    complain_details = (raw_data.get('complain_details') or raw_data.get('Complain Details') or raw_data.get('description') or '').strip()
    customer_mobile_no = (raw_data.get('customer_mobile_no') or raw_data.get('Customer Mobile No.') or '').strip()
    department_name = (raw_data.get('department_name') or raw_data.get('Department Name') or raw_data.get('department') or '').strip()
    category = (raw_data.get('category') or raw_data.get('Category') or '').strip()
    vendor_name = (raw_data.get('vendor_name') or raw_data.get('Vendor Name') or '').strip()
    salesperson_name = (raw_data.get('salesperson_name') or raw_data.get('Salesperson Name') or '').strip()
    status = (raw_data.get('status') or raw_data.get('Status') or 'Submitted').strip()
    closing_date = (raw_data.get('closing_date') or raw_data.get('Closing Date') or '').strip()
    solution_given = (raw_data.get('solution_given') or raw_data.get('Solution Given') or '').strip()
    handled_by = (raw_data.get('handled_by') or raw_data.get('Handled By') or 'Unassigned').strip()
    total_time_taken = (raw_data.get('total_time_taken') or raw_data.get('Total Time Taken') or '').strip()
    comments = (raw_data.get('comments') or raw_data.get('Comments') or '').strip()
    gr_amount = str(raw_data.get('gr_amount') or raw_data.get('GR Amount') or '').strip()
    gr_expected_date = (raw_data.get('gr_expected_date') or raw_data.get('GR Expected Date') or '').strip()
    attachment_link = (raw_data.get('attachment_link') or raw_data.get('Attachment Link') or '').strip()
    from_department = (raw_data.get('from_department') or raw_data.get('From Department') or 'None').strip()

    # Form Validation
    if not customer_name:
        return jsonify({'status': 'error', 'message': 'Customer Name is required.'}), 400
    if not department_name:
        return jsonify({'status': 'error', 'message': 'Department Name is required.'}), 400
    if not complain_types:
        return jsonify({'status': 'error', 'message': 'Complain Types is required.'}), 400
    if not complain_details:
        return jsonify({'status': 'error', 'message': 'Complain Details is required.'}), 400
    if from_department not in ('Online', 'Offline', 'Packing', 'None'):
        return jsonify({'status': 'error', 'message': f"Invalid From Department '{from_department}'. Must be one of: Online, Offline, Packing, None."}), 400

    if gr_amount:
        try:
            val = float(gr_amount)
            if val < 0:
                return jsonify({'status': 'error', 'message': 'GR Amount must be a positive number.'}), 400
        except ValueError:
            return jsonify({'status': 'error', 'message': 'GR Amount must be a valid numeric amount.'}), 400

    # Duplicate submission guard (10-second debounce window per user/subject/description)
    sub_key = f"{user['email']}:{complain_types.lower()}:{complain_details.lower()}"
    now_ts = time.time()
    with _submission_lock:
        last_ts = _recent_submissions.get(sub_key, 0)
        if now_ts - last_ts < 10:
            return jsonify({
                'status': 'error',
                'message': 'Duplicate complaint detected. Please wait a moment before submitting identical details.'
            }), 409
        _recent_submissions[sub_key] = now_ts

    # Process attachment if provided (file upload or direct link)
    if file_obj and file_obj.filename:
        try:
            upload_res = upload_attachment(file_obj)
            if upload_res:
                attachment_link = upload_res.get('web_link', '')
        except (ValueError, RuntimeError) as r_err:
            logger.error(f"Attachment upload failed: {r_err}")
            return jsonify({'status': 'error', 'message': str(r_err)}), 400
        except Exception as e:
            logger.error(f"Unexpected error uploading attachment: {e}")
            return jsonify({
                'status': 'error',
                'message': f"Google Drive attachment upload failed: {str(e)}. Please retry."
            }), 502

    db = get_db()
    try:
        record = db.lodge_complaint(
            employee_name=employee_name,
            complain_date=complain_date,
            customer_name=customer_name,
            invoice_no=invoice_no,
            complain_types=complain_types,
            priority=priority,
            remarks=remarks,
            complain_details=complain_details,
            customer_mobile_no=customer_mobile_no,
            department_name=department_name,
            category=category,
            vendor_name=vendor_name,
            salesperson_name=salesperson_name,
            status=status,
            closing_date=closing_date,
            solution_given=solution_given,
            handled_by=handled_by,
            total_time_taken=total_time_taken,
            comments=comments,
            gr_amount=gr_amount,
            gr_expected_date=gr_expected_date,
            attachment_link=attachment_link,
            from_department=from_department,
            complainant_email=user['email']
        )

        try:
            db.log_audit_event(
                user_id=user.get('user_id'),
                user_email=user.get('email'),
                user_role=user.get('role'),
                action='COMPLAINT_CREATED',
                module='COMPLAINTS',
                record_id=record['Complaint ID'],
                department=record.get('Department Name'),
                details=f"Complaint {record['Complaint ID']} created for customer '{customer_name}'.",
                new_state=json.dumps(record),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not write audit log for complaint creation: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"Complaint {record['Complaint ID']} lodged successfully! It has been saved in Google Sheets.",
            'complaint': record
        }), 201
    except Exception as e:
        with _submission_lock:
            _recent_submissions.pop(sub_key, None)
        logger.error(f"Error lodging complaint: {e}")
        return jsonify({
            'status': 'error',
            'message': f"Failed to save complaint to Google Sheets: {str(e)}"
        }), 500

@app.route('/api/complaints/<complaint_id>', methods=['PUT'])
@app.route('/api/complaints/<complaint_id>/update', methods=['POST'])
@login_required
def update_complaint_endpoint(complaint_id):
    """
    Updates all 21 complaint fields.
    Enforces centralized RBAC and record-level authorization.
    """
    user = get_current_user()
    db = get_db()
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    try:
        custom_roles = db.get_all_roles()
    except Exception:
        custom_roles = None

    allowed, reason = RBACService.can_access_complaint(user, c, action='edit', custom_roles=custom_roles)
    if not allowed:
        return jsonify({'status': 'error', 'message': reason}), 403

    if request.is_json:
        raw_data = request.get_json(silent=True) or {}
    else:
        raw_data = request.form.to_dict()

    # Validate GR Amount if provided
    gr_amount = raw_data.get('gr_amount') or raw_data.get('GR Amount')
    if gr_amount:
        try:
            val = float(gr_amount)
            if val < 0:
                return jsonify({'status': 'error', 'message': 'GR Amount must be a positive number.'}), 400
        except ValueError:
            return jsonify({'status': 'error', 'message': 'GR Amount must be a valid numeric amount.'}), 400

    # Validate From Department if provided
    from_dept = raw_data.get('from_department') or raw_data.get('From Department')
    if from_dept is not None:
        from_dept = str(from_dept).strip()
        if from_dept and from_dept not in ('Online', 'Offline', 'Packing', 'None'):
            return jsonify({'status': 'error', 'message': f"Invalid From Department '{from_dept}'. Must be one of: Online, Offline, Packing, None."}), 400

    try:
        updated = db.update_complaint(complaint_id, raw_data, user)
        try:
            db.log_audit_event(
                user_id=user.get('user_id'),
                user_email=user.get('email'),
                user_role=user.get('role'),
                action='COMPLAINT_UPDATED',
                module='COMPLAINTS',
                record_id=complaint_id,
                department=c.get('Department Name'),
                details=f"Complaint {complaint_id} updated.",
                previous_state=json.dumps(c),
                new_state=json.dumps(updated),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"Complaint {complaint_id} updated successfully.",
            'complaint': updated
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        logger.error(f"Error updating complaint {complaint_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to update complaint in Google Sheets: {str(e)}"}), 500

@app.route('/api/complaints/<complaint_id>', methods=['DELETE'])
@app.route('/api/complaints/<complaint_id>/delete', methods=['POST'])
@login_required
def delete_complaint_endpoint(complaint_id):
    """
    Deletes a complaint permanently from Google Sheets.
    Enforces RBAC delete permission and record authorization.
    Records audit log entries in both Status History and Audit_Logs.
    """
    user = get_current_user()
    db = get_db()
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    try:
        custom_roles = db.get_all_roles()
    except Exception:
        custom_roles = None

    allowed, reason = RBACService.can_access_complaint(user, c, action='delete', custom_roles=custom_roles)
    if not allowed:
        return jsonify({'status': 'error', 'message': reason}), 403

    try:
        db.delete_complaint(complaint_id, user)
        try:
            db.log_audit_event(
                user_id=user.get('user_id'),
                user_email=user.get('email'),
                user_role=user.get('role'),
                action='COMPLAINT_DELETED',
                module='COMPLAINTS',
                record_id=complaint_id,
                department=c.get('Department Name'),
                details=f"Complaint {complaint_id} permanently deleted.",
                previous_state=json.dumps(c),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not write audit log: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"Complaint {complaint_id} has been permanently deleted and logged to the audit trail."
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        logger.error(f"Error deleting complaint {complaint_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete complaint from Google Sheets: {str(e)}"}), 500

# =============================================================================
# SUPER ADMIN BULK COMPLAINT DELETE & RECOVERY APIS
# =============================================================================

@app.route('/api/complaints/years', methods=['GET'])
@login_required
@permission_required('complaints:view')
def get_complaint_years():
    """
    Returns unique years extracted strictly from 'Complain Date' in descending order.
    """
    try:
        user = get_current_user()
        db = get_db()
        complaints = db.get_complaints_for_user(
            user['email'],
            user['role'],
            user_departments=user.get('departments'),
            user_dict=user
        )
        years_set = set()
        for c in complaints:
            dt = ReportService.parse_date(c.get('Complain Date'))
            if dt:
                years_set.add(str(dt.year))

        current_year = str(datetime.datetime.now(datetime.timezone.utc).year)
        years_set.add(current_year)

        sorted_years = sorted(list(years_set), reverse=True)
        return jsonify({
            'status': 'success',
            'years': sorted_years
        })
    except Exception as e:
        logger.error(f"Error fetching complaint years: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to fetch years: {str(e)}"}), 500

@app.route('/api/complaints/bulk-delete/preview', methods=['POST'])
@login_required
@super_admin_required
def preview_bulk_delete():
    """
    Exclusively for Super Admin.
    Rechecks records to be deleted matching the year filter and/or selected complaint IDs.
    Calculates summary metrics, date range, counts by status and department.
    """
    try:
        user = get_current_user()
        db = get_db()
        data = request.get_json(silent=True) or {}
        year = str(data.get('year') or '').strip()
        selected_ids = data.get('complaint_ids')
        if isinstance(selected_ids, list):
            selected_ids = [str(x).strip() for x in selected_ids if str(x).strip()]
        else:
            selected_ids = None

        all_complaints = db.get_all_complaints()

        matched_records = []
        for c in all_complaints:
            cid = c.get('Complaint ID', '').strip()
            if selected_ids is not None and cid not in selected_ids:
                continue

            # Strict Complain Date Year check
            if year and year.upper() != 'ALL':
                c_date = c.get('Complain Date', '')
                dt = ReportService.parse_date(c_date)
                if not dt or str(dt.year) != year:
                    continue

            matched_records.append(c)

        if not matched_records:
            return jsonify({
                'status': 'success',
                'matched_count': 0,
                'year': year,
                'complaint_ids': [],
                'earliest_date': None,
                'latest_date': None,
                'status_summary': {},
                'department_summary': {},
                'message': 'No complaints found matching the specified criteria.'
            })

        dates = []
        status_counts = {}
        dept_counts = {}
        matched_ids = []

        for c in matched_records:
            cid = c.get('Complaint ID', '').strip()
            matched_ids.append(cid)
            dt = ReportService.parse_date(c.get('Complain Date'))
            if dt:
                dates.append(dt)
            st = c.get('Status') or 'Submitted'
            status_counts[st] = status_counts.get(st, 0) + 1
            dept = c.get('Department Name') or 'General'
            dept_counts[dept] = dept_counts.get(dept, 0) + 1

        earliest_dt = min(dates).strftime('%Y-%m-%d') if dates else None
        latest_dt = max(dates).strftime('%Y-%m-%d') if dates else None

        return jsonify({
            'status': 'success',
            'matched_count': len(matched_records),
            'year': year,
            'complaint_ids': matched_ids,
            'earliest_date': earliest_dt,
            'latest_date': latest_dt,
            'status_summary': status_counts,
            'department_summary': dept_counts,
            'sample_complaints': matched_records[:10]
        })
    except Exception as e:
        logger.error(f"Error previewing bulk delete: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to preview bulk delete: {str(e)}"}), 500

@app.route('/api/complaints/bulk-delete/export-backup', methods=['POST', 'GET'])
@login_required
@super_admin_required
def export_bulk_delete_backup():
    """
    Exclusively for Super Admin.
    Generates a full Excel (.xlsx) backup of the complaints targeted for deletion.
    """
    try:
        user = get_current_user()
        db = get_db()
        data = request.get_json(silent=True) or request.args
        year = str(data.get('year') or '').strip()
        selected_ids = data.get('complaint_ids')
        if isinstance(selected_ids, str):
            selected_ids = [x.strip() for x in selected_ids.split(',') if x.strip()]
        elif not isinstance(selected_ids, list):
            selected_ids = None

        all_complaints = db.get_all_complaints()
        matched = []
        for c in all_complaints:
            cid = c.get('Complaint ID', '').strip()
            if selected_ids is not None and cid not in selected_ids:
                continue
            if year and year.upper() != 'ALL':
                dt = ReportService.parse_date(c.get('Complain Date'))
                if not dt or str(dt.year) != year:
                    continue
            matched.append(c)

        if not matched:
            return jsonify({'status': 'error', 'message': 'No complaints found to export.'}), 404

        columns = [
            {'id': 'Complaint ID', 'label': 'Complaint ID'},
            {'id': 'Complain Date', 'label': 'Complain Date'},
            {'id': 'Timestamp', 'label': 'Created Timestamp'},
            {'id': 'Customer Name', 'label': 'Customer Name'},
            {'id': 'Customer Mobile No.', 'label': 'Customer Mobile'},
            {'id': 'Invoice No.', 'label': 'Invoice No.'},
            {'id': 'Complain Types', 'label': 'Type / Issue'},
            {'id': 'Department Name', 'label': 'Department'},
            {'id': 'From Department', 'label': 'From Department'},
            {'id': 'Priority', 'label': 'Priority'},
            {'id': 'Status', 'label': 'Status'},
            {'id': 'Vendor Name', 'label': 'Vendor'},
            {'id': 'Salesperson Name', 'label': 'Salesperson'},
            {'id': 'Handled By', 'label': 'Handled By'},
            {'id': 'GR Amount', 'label': 'GR Amount'},
            {'id': 'Remarks', 'label': 'Remarks'},
            {'id': 'Complain Details', 'label': 'Details'}
        ]

        title = f"Complaint Deletion Backup — Year {year or 'All'}"
        summary = f"Generated by Super Admin ({user.get('email')}) before bulk deletion. Total records: {len(matched)}."
        excel_bytes = ReportService.generate_excel(
            records=matched,
            columns=columns,
            report_title=title,
            filter_summary=summary,
            user_name=user.get('name', 'Super Admin')
        )

        timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
        filename = f"complaints_backup_year_{year or 'all'}_{timestamp_str}.xlsx"

        return send_file(
            io.BytesIO(excel_bytes),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error exporting bulk delete backup: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to export backup: {str(e)}"}), 500

@app.route('/api/complaints/bulk-delete', methods=['POST'])
@login_required
@super_admin_required
def execute_bulk_delete():
    """
    Exclusively for Super Admin.
    Executes atomic bulk deletion of complaints with:
    - Confirmation verification ('DELETE')
    - Revalidation of records and year alignment
    - Automated recoverable backup saved to Deleted_Complaints_Backup
    - Comprehensive audit logging
    """
    try:
        user = get_current_user()
        db = get_db()
        data = request.get_json(silent=True) or {}
        year = str(data.get('year') or '').strip()
        confirmation = str(data.get('confirmation') or '').strip().upper()
        complaint_ids = data.get('complaint_ids')

        # Double safety check: explicit confirmation required
        if confirmation != 'DELETE' and confirmation != f"DELETE {year}":
            return jsonify({
                'status': 'error',
                'message': "Action aborted. Explicit confirmation phrase 'DELETE' is required to execute bulk deletion."
            }), 400

        # If complaint_ids not directly provided, find all complaints matching the year
        if not complaint_ids:
            if not year or year.upper() == 'ALL':
                return jsonify({
                    'status': 'error',
                    'message': "Please specify either a specific Year filter or a list of complaint IDs to delete."
                }), 400
            all_comp = db.get_all_complaints()
            complaint_ids = []
            for c in all_comp:
                dt = ReportService.parse_date(c.get('Complain Date'))
                if dt and str(dt.year) == year:
                    complaint_ids.append(c.get('Complaint ID'))

        if not complaint_ids:
            return jsonify({
                'status': 'error',
                'message': "No complaints found to delete for the specified criteria."
            }), 400

        result = db.bulk_delete_complaints(
            complaint_ids=complaint_ids,
            deleted_by_user=user,
            reason=f"Super Admin bulk delete (Year: {year or 'All'})",
            year=year
        )

        return jsonify({
            'status': 'success',
            'message': f"Successfully deleted {result['deleted_count']} complaint(s) for Year {year or 'All'}. Recoverable backup created: {result['backup_id']}.",
            'data': result
        })
    except ValueError as ve:
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error executing bulk delete: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to execute bulk deletion: {str(e)}"}), 500

@app.route('/api/complaints/bulk-delete/backups', methods=['GET'])
@login_required
@super_admin_required
def list_bulk_delete_backups():
    """
    Exclusively for Super Admin.
    Lists all bulk deletion backups available in Deleted_Complaints_Backup worksheet.
    """
    try:
        db = get_db()
        backups = db.get_bulk_delete_backups()
        return jsonify({
            'status': 'success',
            'backups': backups
        })
    except Exception as e:
        logger.error(f"Error fetching bulk delete backups: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to fetch backups: {str(e)}"}), 500

@app.route('/api/complaints/bulk-delete/restore', methods=['POST'])
@login_required
@super_admin_required
def restore_bulk_delete():
    """
    Exclusively for Super Admin.
    Restores complaints from a recoverable backup in Deleted_Complaints_Backup.
    """
    try:
        user = get_current_user()
        db = get_db()
        data = request.get_json(silent=True) or {}
        backup_id = str(data.get('backup_id') or '').strip()
        if not backup_id:
            return jsonify({'status': 'error', 'message': 'Backup ID is required.'}), 400

        result = db.restore_bulk_deleted_complaints(backup_id=backup_id, restored_by_user=user)
        return jsonify({
            'status': 'success',
            'message': f"Successfully restored {result['restored_count']} complaint(s) from backup {backup_id}.",
            'data': result
        })
    except ValueError as ve:
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error restoring bulk delete: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to restore complaints: {str(e)}"}), 500

@app.route('/api/complaints', methods=['GET'])
@app.route('/api/complaints/my', methods=['GET'])
@login_required
@permission_required('complaints:view')
def list_complaints():
    """
    Returns complaints respecting centralized RBAC scope.
    Supports query parameters:
    - scope: 'my' (assigned to or created by user), 'assigned' (assigned only), 'created' (created only), 'all' (default)
    - department / dept: department filter
    - status: status filter
    - priority: priority filter
    - search / q: full-text search across all complaint attributes
    - page & limit: optional pagination
    """
    user = get_current_user()
    db = get_db()
    try:
        raw_scope = (request.args.get('scope') or '').strip().lower()
        is_my_scope = request.path.endswith('/my') or raw_scope in ('my', 'assigned', 'created')

        all_complaints = db.get_complaints_for_user(
            user['email'],
            user['role'],
            user_departments=user.get('departments'),
            user_dict=user
        )

        # Date filters strictly based on Complain Date
        from_date_arg = (request.args.get('from_date') or request.args.get('start_date') or '').strip()
        to_date_arg = (request.args.get('to_date') or request.args.get('end_date') or '').strip()
        month_year_arg = (request.args.get('month_year') or '').strip()
        year_arg = (request.args.get('year') or '').strip()
        vendor_arg = (request.args.get('vendor') or request.args.get('vendor_name') or '').strip().lower()
        salesperson_arg = (request.args.get('salesperson') or request.args.get('salesperson_name') or '').strip().lower()
        type_arg = (request.args.get('complaint_type') or request.args.get('type') or '').strip().lower()
        handled_arg = (request.args.get('handled_by') or request.args.get('handled') or '').strip().lower()
        category_arg = (request.args.get('category') or '').strip().lower()
        invoice_arg = (request.args.get('invoice_no') or request.args.get('invoice') or '').strip().lower()
        customer_arg = (request.args.get('customer') or request.args.get('customer_name') or '').strip().lower()
        min_amt_arg = request.args.get('min_amount', type=float)
        max_amt_arg = request.args.get('max_amount', type=float)

        from_date_dt = ReportService.parse_date(from_date_arg) if from_date_arg else None
        to_date_dt = ReportService.parse_date(to_date_arg) if to_date_arg else None

        filtered = []
        for c in all_complaints:
            # 1. Scope filter: My Records (Assigned to Me OR Created by Me)
            if request.path.endswith('/my') or raw_scope == 'my':
                if not (RBACService.is_handled_by_user(user, c) or RBACService.is_created_by_user(user, c)):
                    continue
            elif raw_scope == 'assigned':
                if not RBACService.is_handled_by_user(user, c):
                    continue
            elif raw_scope == 'created':
                if not RBACService.is_created_by_user(user, c):
                    continue

            # 2. CRITICAL Complain Date Filter: strictly uses Complain Date, NEVER Created At / Timestamp
            c_date_dt = ReportService.parse_date(c.get('Complain Date'))
            if from_date_dt:
                if not c_date_dt or c_date_dt < from_date_dt:
                    continue
            if to_date_dt:
                if not c_date_dt or c_date_dt > to_date_dt:
                    continue
            if month_year_arg and month_year_arg.upper() != 'ALL':
                if not c_date_dt or c_date_dt.strftime('%Y-%m') != month_year_arg:
                    continue
            if year_arg and year_arg.upper() != 'ALL':
                if not c_date_dt or str(c_date_dt.year) != year_arg:
                    continue

            # 3. Department filter
            dept_arg = (request.args.get('department') or request.args.get('dept') or '').strip()
            if dept_arg and dept_arg.upper() != 'ALL':
                c_depts = [d.lower() for d in RBACService.parse_complaint_departments(c)]
                if dept_arg.lower() not in c_depts:
                    continue

            # 4. Status filter
            status_arg = (request.args.get('status') or '').strip()
            if status_arg and status_arg.upper() != 'ALL':
                if (c.get('Status') or '').strip().lower() != status_arg.lower():
                    continue

            # 5. Priority filter
            prio_arg = (request.args.get('priority') or '').strip()
            if prio_arg and prio_arg.upper() != 'ALL':
                if (c.get('Priority') or '').strip().lower() != prio_arg.lower():
                    continue

            # 6. From Department filter (supports comma-separated multi-selection)
            from_dept_arg = (request.args.get('from_department') or request.args.get('from_dept') or '').strip()
            if from_dept_arg and from_dept_arg.upper() != 'ALL':
                allowed_from_depts = [fd.strip().lower() for fd in from_dept_arg.split(',') if fd.strip()]
                c_from_dept = (c.get('From Department') or c.get('from_department') or '').strip().lower()
                if not c_from_dept:
                    c_from_dept = 'none'
                if c_from_dept not in allowed_from_depts:
                    continue

            # 7. Vendor filter
            if vendor_arg:
                c_vendor = (c.get('Vendor Name') or '').lower()
                if vendor_arg not in c_vendor:
                    continue

            # 8. Salesperson filter
            if salesperson_arg:
                c_sp = (c.get('Salesperson Name') or '').lower()
                if salesperson_arg not in c_sp:
                    continue

            # 9. Type filter
            if type_arg:
                c_type = (c.get('Complain Types') or c.get('Subject') or '').lower()
                if type_arg not in c_type:
                    continue

            # 10. Handled By filter
            if handled_arg:
                c_handled = (c.get('Handled By') or c.get('Assigned To Name') or '').lower()
                if handled_arg not in c_handled:
                    continue

            # 11. Category filter
            if category_arg:
                c_cat = (c.get('Category') or '').lower()
                if category_arg not in c_cat:
                    continue

            # 12. Invoice filter
            if invoice_arg:
                c_inv = (c.get('Invoice No.') or '').lower()
                if invoice_arg not in c_inv:
                    continue

            # 13. Customer / Mobile filter
            if customer_arg:
                c_cust = (c.get('Customer Name') or c.get('Complainant Name') or '').lower()
                c_mob = str(c.get('Customer Mobile No.') or '').lower()
                if customer_arg not in c_cust and customer_arg not in c_mob:
                    continue

            # 14. Amount filter
            if min_amt_arg is not None:
                c_amt = ReportService.parse_float(c.get('GR Amount'))
                if c_amt < min_amt_arg:
                    continue
            if max_amt_arg is not None:
                c_amt = ReportService.parse_float(c.get('GR Amount'))
                if c_amt > max_amt_arg:
                    continue

            # 15. Search query
            search_arg = (request.args.get('search') or request.args.get('q') or '').strip().lower()
            if search_arg:
                search_blob = ' '.join([
                    str(c.get('Complaint ID') or ''),
                    str(c.get('Customer Name') or c.get('Complainant Name') or ''),
                    str(c.get('Customer Mobile No.') or ''),
                    str(c.get('Invoice No.') or ''),
                    str(c.get('Complain Types') or c.get('Subject') or ''),
                    str(c.get('Department Name') or c.get('Department') or ''),
                    str(c.get('From Department') or ''),
                    str(c.get('Complain Date') or ''),
                    str(c.get('Handled By') or c.get('Assigned To Name') or ''),
                    str(c.get('Employee Name') or ''),
                    str(c.get('Vendor Name') or ''),
                    str(c.get('Salesperson Name') or ''),
                    str(c.get('Remarks') or ''),
                    str(c.get('Solution Given') or ''),
                    str(c.get('Status') or '')
                ]).lower()
                if search_arg not in search_blob:
                    continue

            filtered.append(c)

        # Apply Sorting
        sort_by = (request.args.get('sort_by') or 'date').strip().lower()
        sort_dir = (request.args.get('sort_dir') or request.args.get('order') or 'desc').strip().lower()
        reverse = (sort_dir == 'desc')

        def sort_key(item):
            if sort_by in ('date', 'complain_date'):
                dt = ReportService.parse_date(item.get('Complain Date'))
                return dt.isoformat() if dt else ''
            elif sort_by in ('month', 'month_year'):
                dt = ReportService.parse_date(item.get('Complain Date'))
                return dt.strftime('%Y-%m') if dt else ''
            elif sort_by in ('customer', 'name'):
                return (item.get('Customer Name') or item.get('Complainant Name') or '').lower()
            elif sort_by == 'id':
                return item.get('Complaint ID') or ''
            elif sort_by == 'invoice':
                return (item.get('Invoice No.') or '').lower()
            elif sort_by == 'type':
                return (item.get('Complain Types') or item.get('Subject') or '').lower()
            elif sort_by == 'department':
                return (item.get('Department Name') or item.get('Department') or '').lower()
            elif sort_by == 'priority':
                weights = {'urgent': 4, 'high': 3, 'medium': 2, 'low': 1}
                return weights.get(str(item.get('Priority') or '').lower(), 0)
            elif sort_by == 'status':
                return (item.get('Status') or '').lower()
            elif sort_by == 'handled':
                return (item.get('Handled By') or item.get('Assigned To Name') or '').lower()
            elif sort_by == 'amount':
                return ReportService.parse_float(item.get('GR Amount'))
            return item.get('Complaint ID') or ''

        filtered.sort(key=sort_key, reverse=reverse)

        total_records = len(filtered)
        page = request.args.get('page', type=int)
        limit = request.args.get('limit', type=int)

        if page and limit and limit > 0:
            total_pages = max(1, math.ceil(total_records / limit))
            page = max(1, min(page, total_pages))
            start = (page - 1) * limit
            end = start + limit
            paged = filtered[start:end]
            return jsonify({
                'status': 'success',
                'complaints': paged,
                'count': len(paged),
                'total_records': total_records,
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total_records': total_records,
                    'total_pages': total_pages
                }
            })

        return jsonify({
            'status': 'success',
            'complaints': filtered,
            'count': len(filtered),
            'total_records': total_records
        })
    except Exception as e:
        logger.error(f"Error listing complaints: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to read complaints: {str(e)}"}), 500

@app.route('/api/complaints/<complaint_id>', methods=['GET'])
@login_required
@permission_required('complaints:view')
def get_complaint_details(complaint_id):
    """
    Retrieves full details, status timeline, and comments for a single complaint.
    Strictly verifies record-level RBAC access to prevent IDOR / cross-department direct URL access.
    """
    user = get_current_user()
    db = get_db()
    try:
        c = db.get_complaint_by_id(complaint_id)
        if not c:
            return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

        try:
            custom_roles = db.get_all_roles()
        except Exception:
            custom_roles = None

        allowed, reason = RBACService.can_access_complaint(user, c, action='view', custom_roles=custom_roles)
        if not allowed:
            return jsonify({'status': 'error', 'message': f"Access forbidden: {reason}"}), 403

        history = db.get_status_history(complaint_id)
        comments = db.get_comments(complaint_id, user_role=user['role'], user_dict=user)

        return jsonify({
            'status': 'success',
            'complaint': c,
            'history': history,
            'comments': comments
        })
    except Exception as e:
        logger.error(f"Error fetching complaint details: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to read complaint details: {str(e)}"}), 500

@app.route('/api/complaints/<complaint_id>/status', methods=['POST', 'PUT'])
@login_required
@permission_required('complaints:change_status')
def update_status(complaint_id):
    """Staff/Admin updates status of a complaint."""
    user = get_current_user()
    data = request.get_json(silent=True) or request.form or {}
    new_status = (data.get('status') or '').strip()
    remarks = (data.get('remarks') or '').strip()

    valid_statuses = ('Submitted', 'Under Review', 'In Progress', 'Resolved', 'Rejected', 'Closed')
    if new_status not in valid_statuses:
        return jsonify({
            'status': 'error',
            'message': f"Invalid status '{new_status}'. Allowed statuses: {', '.join(valid_statuses)}"
        }), 400

    db = get_db()
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    try:
        custom_roles = db.get_all_roles()
    except Exception:
        custom_roles = None

    # Determine specific workflow action (change_status vs close vs reopen)
    curr_status = c.get('Status') or 'Submitted'
    if new_status == 'Closed':
        action = 'close'
    elif curr_status == 'Closed' and new_status != 'Closed':
        action = 'reopen'
    else:
        action = 'change_status'

    allowed, reason = RBACService.can_access_complaint(user, c, action=action, custom_roles=custom_roles)
    if not allowed:
        return jsonify({'status': 'error', 'message': reason}), 403

    try:
        db.update_complaint_status(
            complaint_id=complaint_id,
            new_status=new_status,
            changed_by_user=user,
            remarks=remarks
        )
        try:
            db.log_audit_event(
                user_id=user.get('user_id'),
                user_email=user.get('email'),
                user_role=user.get('role'),
                action='STATUS_CHANGED',
                module='COMPLAINTS',
                record_id=complaint_id,
                department=c.get('Department Name'),
                details=f"Status changed from '{c.get('Status')}' to '{new_status}'. Remarks: {remarks}",
                previous_state=c.get('Status'),
                new_state=new_status,
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"Complaint {complaint_id} status updated to '{new_status}'."
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to update status in Google Sheets: {str(e)}"}), 500

@app.route('/api/complaints/<complaint_id>/assign', methods=['POST'])
@login_required
@permission_required('complaints:assign')
def assign_complaint(complaint_id):
    """Staff/Admin assigns a complaint to a support staff member."""
    user = get_current_user()
    data = request.get_json(silent=True) or request.form or {}
    assigned_to_email = (data.get('assigned_to_email') or '').strip().lower()
    assigned_to_name = (data.get('assigned_to_name') or '').strip()

    if not assigned_to_email:
        return jsonify({'status': 'error', 'message': 'Assignee email is required.'}), 400

    db = get_db()
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    try:
        custom_roles = db.get_all_roles()
    except Exception:
        custom_roles = None

    allowed, reason = RBACService.can_access_complaint(user, c, action='assign', custom_roles=custom_roles)
    if not allowed:
        return jsonify({'status': 'error', 'message': reason}), 403

    if not assigned_to_name:
        # Lookup user name from Users database
        assignee = db.find_user_by_email(assigned_to_email)
        assigned_to_name = assignee.get('Full Name') if assignee else assigned_to_email

    try:
        db.assign_complaint(
            complaint_id=complaint_id,
            assigned_to_name=assigned_to_name,
            assigned_to_email=assigned_to_email,
            assigned_by_user=user
        )
        try:
            db.log_audit_event(
                user_id=user.get('user_id'),
                user_email=user.get('email'),
                user_role=user.get('role'),
                action='COMPLAINT_ASSIGNED',
                module='COMPLAINTS',
                record_id=complaint_id,
                department=c.get('Department Name'),
                details=f"Assigned to {assigned_to_name} ({assigned_to_email}).",
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"Complaint {complaint_id} assigned to {assigned_to_name}."
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        logger.error(f"Error assigning complaint: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to assign complaint: {str(e)}"}), 500

@app.route('/api/complaints/<complaint_id>/comments', methods=['POST'])
@login_required
def add_comment(complaint_id):
    """Adds a comment / note to a complaint."""
    user = get_current_user()
    data = request.get_json(silent=True) or request.form or {}
    message = (data.get('message') or '').strip()
    comment_type = data.get('comment_type', 'Public Update')

    if not message:
        return jsonify({'status': 'error', 'message': 'Comment message cannot be empty.'}), 400

    db = get_db()
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    try:
        custom_roles = db.get_all_roles()
    except Exception:
        custom_roles = None

    allowed, reason = RBACService.can_access_complaint(user, c, action='view', custom_roles=custom_roles)
    if not allowed:
        return jsonify({'status': 'error', 'message': reason}), 403

    if comment_type == 'Internal Note':
        if not RBACService.has_permission(user, 'comments:add_internal', custom_roles=custom_roles):
            return jsonify({
                'status': 'error',
                'message': "Access forbidden. Required permission: 'comments:add_internal'."
            }), 403
    else:
        if not RBACService.has_permission(user, 'comments:add', custom_roles=custom_roles):
            return jsonify({
                'status': 'error',
                'message': "Access forbidden. Required permission: 'comments:add'."
            }), 403

    try:
        new_comment = db.add_comment(
            complaint_id=complaint_id,
            author_name=user['name'],
            author_email=user['email'],
            author_role=user['role'],
            comment_type=comment_type,
            message=message
        )
        try:
            db.log_audit_event(
                user_id=user.get('user_id'),
                user_email=user.get('email'),
                user_role=user.get('role'),
                action='COMMENT_ADDED',
                module='COMMENTS',
                record_id=complaint_id,
                department=c.get('Department Name'),
                details=f"Added {comment_type}: '{message[:40]}...'",
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': 'Comment posted successfully.',
            'comment': new_comment
        }), 201
    except Exception as e:
        logger.error(f"Error adding comment: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to post comment: {str(e)}"}), 500

# =============================================================================
# USER MANAGEMENT APIS (ADMIN ONLY)
# =============================================================================

@app.route('/api/users', methods=['GET'])
@login_required
@permission_required('users:view')
def list_users():
    """Lists all users (excluding password hashes)."""
    db = get_db()
    try:
        users = db.get_all_users()
        return jsonify({'status': 'success', 'users': users})
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to list users: {str(e)}"}), 500

@app.route('/api/users', methods=['POST'])
@login_required
@permission_required('users:create')
def admin_create_user():
    """Admin / Super Admin creates a staff member or user."""
    data = request.get_json(silent=True) or request.form or {}
    full_name = (data.get('full_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()
    role = (data.get('role') or 'Department Staff').strip()

    raw_dept = data.get('department') or data.get('departments') or 'General'
    if isinstance(raw_dept, list):
        department = ', '.join([d.strip() for d in raw_dept if d.strip()])
    else:
        department = str(raw_dept).strip()

    raw_perms = data.get('custom_permissions')
    if isinstance(raw_perms, list):
        custom_permissions = ', '.join([str(p).strip() for p in raw_perms if str(p).strip()])
    elif raw_perms:
        custom_permissions = str(raw_perms).strip()
    else:
        custom_permissions = ''

    current_user = get_current_user()
    db = get_db()

    try:
        custom_roles = db.get_all_roles()
    except Exception:
        custom_roles = None

    role_cfg = RBACService.get_role_template(role, custom_roles=custom_roles)
    if not role_cfg:
        return jsonify({'status': 'error', 'message': f"Invalid role '{role}'."}), 400

    # Privilege escalation guard
    allowed, reason = RBACService.can_assign_role(current_user, role_cfg['name'], custom_roles=custom_roles)
    if not allowed:
        return jsonify({'status': 'error', 'message': reason}), 403

    if not full_name or not email or not password:
        return jsonify({'status': 'error', 'message': 'Full name, email, and password are required.'}), 400

    if len(password) < 6:
        return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters.'}), 400

    try:
        user = db.create_user(
            full_name=full_name,
            email=email,
            password=password,
            role=role_cfg['name'],
            department=department,
            custom_permissions=custom_permissions
        )
        try:
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action='USER_CREATED',
                module='USERS',
                record_id=user.get('User ID'),
                department=department,
                details=f"Created user '{full_name}' ({email}) with role '{role_cfg['name']}'.",
                new_state=json.dumps(user),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record user creation audit: {audit_err}")

        return jsonify({'status': 'success', 'message': f"User '{full_name}' created.", 'user': user}), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to create user: {str(e)}"}), 500

@app.route('/api/users/<user_id>', methods=['GET'])
@login_required
@permission_required('users:view')
def get_user_details(user_id):
    """Retrieves safe user details for editing (excluding password hash)."""
    db = get_db()
    u = db.find_user_by_id(user_id)
    if not u:
        return jsonify({'status': 'error', 'message': f"User '{user_id}' not found."}), 404
    safe_u = dict(u)
    safe_u.pop('Password Hash', None)
    return jsonify({'status': 'success', 'user': safe_u})

@app.route('/api/users/<user_id>', methods=['PUT'])
@app.route('/api/users/<user_id>/update', methods=['POST'])
@login_required
@permission_required('users:edit')
def update_user_endpoint(user_id):
    """
    Updates an existing user.
    Enforces RBAC & prevents unauthorized privilege escalation:
    - Super Admin & Admin can edit users.
    - Only Super Admin can edit Super Admin accounts.
    - Non-super admins cannot grant permissions or roles they lack.
    - Users cannot deactivate their own active account.
    - Cannot deactivate the last remaining active Super Admin.
    - Validates email format and uniqueness.
    - Supports secure password reset without retrieving/exposing existing passwords.
    """
    current_user = get_current_user()
    db = get_db()
    target_user = db.find_user_by_id(user_id)
    if not target_user:
        return jsonify({'status': 'error', 'message': f"User '{user_id}' not found."}), 404

    data = request.get_json(silent=True) or request.form or {}
    full_name = (data.get('full_name') or target_user.get('Full Name', '')).strip()
    email = (data.get('email') or target_user.get('Email', '')).strip().lower()
    role = (data.get('role') or target_user.get('Role', 'User')).strip()

    raw_dept = data.get('department') if data.get('department') is not None else (data.get('departments') if data.get('departments') is not None else target_user.get('Department', 'General'))
    if isinstance(raw_dept, list):
        department = ', '.join([d.strip() for d in raw_dept if d.strip()])
    else:
        department = str(raw_dept).strip()

    is_active = data.get('is_active')
    new_password = data.get('new_password') or data.get('password')
    custom_permissions = data.get('custom_permissions')

    # Validations
    if not full_name:
        return jsonify({'status': 'error', 'message': 'Full Name is required.'}), 400
    if not email:
        return jsonify({'status': 'error', 'message': 'Email address is required.'}), 400
    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'status': 'error', 'message': 'Please enter a valid email address.'}), 400

    try:
        custom_roles = db.get_all_roles()
    except Exception:
        custom_roles = None

    role_cfg = RBACService.get_role_template(role, custom_roles=custom_roles)
    if not role_cfg:
        return jsonify({'status': 'error', 'message': f"Invalid role '{role}'."}), 400

    # Privilege escalation guards
    is_current_super = RBACService.canonical_role(current_user.get('role')) == 'Super Admin'
    target_is_super = RBACService.canonical_role(target_user.get('Role')) == 'Super Admin'

    if target_is_super and not is_current_super:
        return jsonify({'status': 'error', 'message': 'Access forbidden. Only Super Admins can edit Super Admin accounts.'}), 403

    allowed, reason = RBACService.can_assign_role(current_user, role_cfg['name'], custom_roles=custom_roles)
    if not allowed:
        return jsonify({'status': 'error', 'message': reason}), 403

    # Self-deactivation and self-role escalation guard
    if current_user.get('user_id') == user_id:
        if is_active is not None and str(is_active).upper() in ('FALSE', '0', 'NO'):
            return jsonify({'status': 'error', 'message': 'You cannot deactivate your own active account.'}), 400
        if role_cfg['name'] != target_user.get('Role'):
            return jsonify({'status': 'error', 'message': 'You cannot change your own user role.'}), 403

    # Last Super Admin deactivation guard
    if target_is_super and is_active is not None and str(is_active).upper() in ('FALSE', '0', 'NO'):
        active_supers = db.count_active_super_admins() if hasattr(db, 'count_active_super_admins') else 1
        if active_supers <= 1:
            return jsonify({'status': 'error', 'message': 'Cannot deactivate the system’s sole active Super Admin.'}), 400

    # Optional password reset validation
    if new_password and str(new_password).strip():
        if len(str(new_password).strip()) < 6:
            return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters.'}), 400

    try:
        updated = db.update_user(
            user_id=user_id,
            full_name=full_name,
            email=email,
            role=role_cfg['name'],
            department=department,
            is_active=is_active,
            new_password=new_password,
            custom_permissions=custom_permissions
        )

        try:
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action='USER_UPDATED',
                module='USERS',
                record_id=user_id,
                department=department,
                details=f"Updated user '{full_name}' ({email}).",
                previous_state=json.dumps(target_user),
                new_state=json.dumps(updated),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        # Synchronize active session if current user edited their own details
        if current_user.get('user_id') == user_id:
            current_user['name'] = updated['Full Name']
            current_user['email'] = updated['Email']
            current_user['role'] = updated['Role']
            current_user['department'] = updated['Department']
            current_user['departments'] = RBACService.get_user_departments(updated)
            current_user['permissions'] = list(sorted(RBACService.get_effective_permissions(updated, custom_roles=custom_roles)))
            session['user'] = current_user
            session.modified = True

        return jsonify({
            'status': 'success',
            'message': f"User '{full_name}' updated successfully.",
            'user': updated
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error updating user '{user_id}': {e}")
        return jsonify({'status': 'error', 'message': f"Failed to update user: {str(e)}"}), 500

@app.route('/api/users/<user_id>/status', methods=['PATCH', 'POST'])
@login_required
@permission_required('users:edit')
def toggle_user_status(user_id):
    """Activates or deactivates an existing user account."""
    current_user = get_current_user()
    db = get_db()
    target_user = db.find_user_by_id(user_id)
    if not target_user:
        return jsonify({'status': 'error', 'message': f"User '{user_id}' not found."}), 404

    data = request.get_json(silent=True) or request.form or {}
    new_status = data.get('is_active')
    if new_status is None:
        # Toggle current status
        current_status = str(target_user.get('Is Active', 'TRUE')).upper() == 'TRUE'
        new_status = 'FALSE' if current_status else 'TRUE'

    is_deactivating = str(new_status).upper() in ('FALSE', '0', 'NO')

    # Self-deactivation guard
    if current_user.get('user_id') == user_id and is_deactivating:
        return jsonify({'status': 'error', 'message': 'You cannot deactivate your own active account.'}), 400

    # Super Admin protection
    target_is_super = RBACService.canonical_role(target_user.get('Role')) == 'Super Admin'
    if target_is_super:
        if RBACService.canonical_role(current_user.get('role')) != 'Super Admin':
            return jsonify({'status': 'error', 'message': 'Access forbidden. Only Super Admins can alter Super Admin status.'}), 403
        if is_deactivating:
            active_supers = db.count_active_super_admins() if hasattr(db, 'count_active_super_admins') else 1
            if active_supers <= 1:
                return jsonify({'status': 'error', 'message': 'Cannot deactivate the system’s sole active Super Admin.'}), 400

    try:
        updated = db.update_user(user_id=user_id, is_active=new_status)
        status_label = "activated" if str(updated['Is Active']).upper() == 'TRUE' else "deactivated"
        try:
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action='USER_STATUS_TOGGLED',
                module='USERS',
                record_id=user_id,
                department=target_user.get('Department'),
                details=f"User '{updated['Full Name']}' status changed to {status_label}.",
                previous_state=str(target_user.get('Is Active')),
                new_state=str(updated['Is Active']),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"User '{updated['Full Name']}' has been {status_label}.",
            'user': updated
        })
    except Exception as e:
        logger.error(f"Error updating status for user '{user_id}': {e}")
        return jsonify({'status': 'error', 'message': f"Failed to change user status: {str(e)}"}), 500

@app.route('/api/users/<user_id>/usage', methods=['GET'])
@login_required
@permission_required('users:view')
def get_user_usage_endpoint(user_id):
    """
    Returns activity usage analysis for a user before deletion:
    - Complaints lodged
    - Complaints handled/assigned
    - Comments & history log entries
    """
    db = get_db()
    usage_info = db.get_user_usage(user_id) if hasattr(db, 'get_user_usage') else None
    if not usage_info:
        user = db.find_user_by_id(user_id)
        if not user:
            return jsonify({'status': 'error', 'message': f"User '{user_id}' not found."}), 404
        usage_info = {
            'user': user,
            'lodged_complaints_count': 0,
            'handled_complaints_count': 0,
            'comments_count': 0,
            'history_count': 0,
            'total_activity_count': 0
        }
    safe_user = dict(usage_info['user'])
    safe_user.pop('Password Hash', None)
    return jsonify({
        'status': 'success',
        'user': safe_user,
        'usage': {
            'lodged_complaints_count': usage_info['lodged_complaints_count'],
            'handled_complaints_count': usage_info['handled_complaints_count'],
            'comments_count': usage_info['comments_count'],
            'history_count': usage_info['history_count'],
            'total_activity_count': usage_info['total_activity_count']
        }
    })

@app.route('/api/users/<user_id>', methods=['DELETE'])
@app.route('/api/users/<user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user_endpoint(user_id):
    """
    Deletes or archives an existing user account.
    - Soft Delete: Marks account as 'ARCHIVED' (preserves row in Users sheet, immediately blocks login).
      Allowed for Admin and Super Admin. Admins cannot archive Super Admins.
    - Permanent Delete: Removes user row from Users worksheet.
      Allowed ONLY for Super Admin after explicit confirmation.
    - Self-deletion is strictly blocked (Users cannot delete their own account).
    - Cannot delete or archive the sole active Super Admin account.
    - Preserves all business records (complaints, comments, assignments, status history).
    - Records audit log entry with actor, timestamp, action, and reason.
    """
    current_user = get_current_user()
    db = get_db()
    target_user = db.find_user_by_id(user_id)
    if not target_user:
        return jsonify({'status': 'error', 'message': f"User '{user_id}' not found."}), 404

    data = request.get_json(silent=True) or request.form or {}
    action = (data.get('action') or data.get('delete_type') or 'soft').strip().lower()
    reason = (data.get('reason') or '').strip()
    confirm = (data.get('confirm') or '').strip()

    if not reason:
        return jsonify({'status': 'error', 'message': 'A valid reason for deletion/archival is required for the audit log.'}), 400

    # Self-deletion guard
    curr_id = str(current_user.get('user_id', '')).strip()
    curr_email = str(current_user.get('email', '')).strip().lower()
    targ_id = str(target_user.get('User ID', '')).strip()
    targ_email = str(target_user.get('Email', '')).strip().lower()

    if curr_id == targ_id or (curr_email and curr_email == targ_email):
        return jsonify({'status': 'error', 'message': 'You cannot delete or archive your own logged-in account.'}), 400

    # Super Admin protection
    is_current_super = RBACService.canonical_role(current_user.get('role')) == 'Super Admin'
    target_is_super = RBACService.canonical_role(target_user.get('Role')) == 'Super Admin'

    if target_is_super and not is_current_super:
        return jsonify({'status': 'error', 'message': 'Access forbidden: Only Super Admins can delete or archive a Super Admin account.'}), 403

    # Last active Super Admin guard
    if target_is_super and str(target_user.get('Is Active', '')).upper() == 'TRUE':
        active_supers = db.count_active_super_admins() if hasattr(db, 'count_active_super_admins') else 1
        if active_supers <= 1:
            return jsonify({'status': 'error', 'message': 'Cannot delete or archive the system’s sole active Super Admin.'}), 400

    # Action validations
    if action == 'permanent':
        if not is_current_super:
            return jsonify({'status': 'error', 'message': 'Access forbidden: Permanent account deletion is strictly restricted to Super Admins.'}), 403
        if confirm.upper() != 'DELETE':
            return jsonify({'status': 'error', 'message': 'Permanent deletion requires typing DELETE in the confirmation box.'}), 400
    elif action not in ('soft', 'archive'):
        return jsonify({'status': 'error', 'message': f"Invalid action '{action}'. Allowed actions: 'soft', 'permanent'."}), 400

    try:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')
        now_iso = now_dt.isoformat()

        if action == 'permanent':
            db.delete_user(user_id)
            audit_action = 'USER_PERMANENTLY_DELETED'
            audit_msg = f"Permanently deleted user '{target_user.get('Full Name')}' ({user_id}, {target_user.get('Email')}). Reason: {reason}"
            success_msg = f"User '{target_user.get('Full Name')}' has been permanently deleted. Historical complaint records remain preserved."
        else:
            db.update_user(user_id=user_id, is_active='ARCHIVED')
            audit_action = 'USER_ARCHIVED'
            audit_msg = f"Archived user '{target_user.get('Full Name')}' ({user_id}, {target_user.get('Email')}). Reason: {reason}"
            success_msg = f"User '{target_user.get('Full Name')}' has been archived. Login is disabled and records remain preserved."

        # Write audit trail to Audit_Logs
        try:
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action=audit_action,
                module='USERS',
                record_id=user_id,
                department=target_user.get('Department'),
                details=audit_msg,
                previous_state=json.dumps(target_user),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        # Write audit trail to Status History
        try:
            if hasattr(db, 'add_status_history'):
                db.add_status_history(
                    complaint_id=f"USER:{user_id}",
                    old_status=str(target_user.get('Is Active', 'TRUE')),
                    new_status='ARCHIVED' if action == 'soft' else 'DELETED',
                    changed_by_name=current_user.get('name', 'Admin'),
                    changed_by_email=current_user.get('email', ''),
                    remarks=audit_msg
                )
        except Exception as audit_err:
            logger.warning(f"Could not record user status history: {audit_err}")

        # Write audit trail to System_Config
        try:
            if hasattr(db, 'set_system_config'):
                audit_key = f"AUDIT_USER_{action.upper()}_{user_id}_{int(time.time())}"
                audit_payload = {
                    'action': audit_action,
                    'target_user_id': user_id,
                    'target_name': target_user.get('Full Name'),
                    'target_email': target_user.get('Email'),
                    'target_role': target_user.get('Role'),
                    'actor_email': current_user.get('email'),
                    'actor_name': current_user.get('name'),
                    'reason': reason,
                    'timestamp': now_iso
                }
                db.set_system_config(audit_key, json.dumps(audit_payload), updated_by=current_user.get('email', 'system'))
        except Exception as config_err:
            logger.warning(f"Could not record user audit config: {config_err}")

        return jsonify({
            'status': 'success',
            'action': action,
            'message': success_msg
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error executing user deletion '{user_id}': {e}")
        return jsonify({'status': 'error', 'message': f"Failed to execute user deletion: {str(e)}"}), 500

# =============================================================================
# RBAC ROLES, PERMISSIONS & AUDIT LOGS APIS
# =============================================================================

@app.route('/api/rbac/permissions', methods=['GET'])
@login_required
def get_rbac_permissions():
    """Returns the complete granular permissions taxonomy grouped by category."""
    categories = {}
    for p in ALL_PERMISSIONS:
        cat = p['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(p)

    return jsonify({
        'status': 'success',
        'permissions': ALL_PERMISSIONS,
        'categories': categories,
        'all_permission_ids': sorted(list(ALL_PERMISSION_IDS))
    })

def normalize_role_dict(r, usage_counts=None):
    """
    Normalizes a role record into a consistent structure with both
    camelCase/snake_case and Title Case properties, parsed permissions list,
    boolean system-role flags, and accurate user assignment counts.
    """
    if not isinstance(r, dict):
        return r
    role_id = str(r.get('role_id') or r.get('Role ID') or r.get('id') or '').strip()
    role_name = str(r.get('role_name') or r.get('Name') or r.get('name') or '').strip()
    description = str(r.get('description') or r.get('Description') or '').strip()
    data_scope = str(r.get('data_scope') or r.get('Data Scope') or 'DEPARTMENT').strip().upper()
    if data_scope not in ('ALL', 'DEPARTMENT', 'OWN'):
        data_scope = 'DEPARTMENT'

    # Permissions list
    raw_perms = r.get('permissions') if r.get('permissions') is not None else r.get('Permissions')
    if isinstance(raw_perms, list):
        perms_list = [str(p).strip() for p in raw_perms if str(p).strip()]
    elif isinstance(raw_perms, str):
        perms_list = [p.strip() for p in raw_perms.split(',') if p.strip()]
    else:
        perms_list = []

    # If Super Admin, ensure '*' is in perms_list for full wildcard access representation
    if role_name == 'Super Admin' and '*' not in perms_list:
        perms_list = ['*'] + perms_list

    # System role boolean
    if 'is_system' in r and isinstance(r['is_system'], bool):
        is_system = r['is_system']
    elif 'is_system_role' in r and isinstance(r['is_system_role'], bool):
        is_system = r['is_system_role']
    elif 'Is System Role' in r:
        is_system = str(r['Is System Role']).strip().upper() == 'TRUE'
    else:
        is_system = role_name in DEFAULT_ROLE_TEMPLATES

    # User count
    user_count = 0
    if usage_counts is not None and role_name:
        user_count = usage_counts.get(role_name.lower(), 0)
    elif 'assigned_users_count' in r:
        try:
            user_count = int(r['assigned_users_count'])
        except (ValueError, TypeError):
            user_count = 0
    elif 'assigned_user_count' in r:
        try:
            user_count = int(r['assigned_user_count'])
        except (ValueError, TypeError):
            user_count = 0

    return {
        # Preferred API properties
        'role_id': role_id,
        'role_name': role_name,
        'name': role_name,
        'description': description,
        'data_scope': data_scope,
        'permissions': perms_list,
        'is_system': is_system,
        'is_system_role': is_system,
        'assigned_users_count': user_count,
        'assigned_user_count': user_count,
        # Google Sheets original headers for backward compatibility
        'Role ID': role_id,
        'Name': role_name,
        'Description': description,
        'Data Scope': data_scope,
        'Permissions': perms_list,
        'Is System Role': 'TRUE' if is_system else 'FALSE'
    }

@app.route('/api/rbac/roles', methods=['GET'])
@login_required
def list_roles():
    """Lists all configurable roles and system defaults with live assignment counts."""
    db = get_db()
    try:
        raw_roles = db.get_all_roles() or []

        # Calculate live user assignment counts
        all_users = []
        try:
            all_users = db.get_all_users() or []
        except Exception as ue:
            logger.warning(f"Could not fetch users to compute role counts: {ue}")

        usage_counts = {}
        for u in all_users:
            raw_role = u.get('Role') or u.get('role') or ''
            r_canon = RBACService.canonical_role(raw_role)
            if r_canon:
                key = r_canon.strip().lower()
                usage_counts[key] = usage_counts.get(key, 0) + 1

        roles = [normalize_role_dict(r, usage_counts) for r in raw_roles]
        return jsonify({
            'status': 'success',
            'roles': roles,
            'count': len(roles)
        })
    except Exception as e:
        logger.error(f"Error listing roles: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to list roles: {str(e)}"}), 500

@app.route('/api/rbac/roles/<role_id>', methods=['GET'])
@login_required
def get_role_details(role_id):
    """Retrieves single normalized role definition."""
    db = get_db()
    try:
        r = db.get_role_by_id(role_id)
        if not r:
            return jsonify({'status': 'error', 'message': f"Role '{role_id}' not found."}), 404
        return jsonify({'status': 'success', 'role': normalize_role_dict(r)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/rbac/roles/<role_id>/usage', methods=['GET'])
@login_required
@permission_required('roles:view')
def get_role_usage_endpoint(role_id):
    """Checks how many users are assigned to a role before deletion."""
    db = get_db()
    try:
        role, count, assigned = db.get_role_usage(role_id)
        is_sys = str(role.get('Is System Role', 'FALSE')).upper() == 'TRUE' or bool(role.get('is_system'))

        assigned_user_dicts = []
        for u in assigned:
            if isinstance(u, dict):
                assigned_user_dicts.append({
                    'user_id': u.get('User ID') or u.get('user_id'),
                    'name': u.get('Name') or u.get('name'),
                    'email': u.get('Email') or u.get('email'),
                    'role': u.get('Role') or u.get('role'),
                    'department': u.get('Department') or u.get('department'),
                })
            else:
                assigned_user_dicts.append({'email': str(u)})

        normalized_role = normalize_role_dict(role)
        return jsonify({
            'status': 'success',
            'role': normalized_role,
            'role_name': normalized_role.get('role_name', ''),
            'is_system_role': is_sys,
            'assigned_user_count': count,
            'assigned_users_count': count,
            'assigned_users': assigned_user_dicts,
            'can_delete': (not is_sys and count == 0)
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/rbac/roles', methods=['POST'])
@login_required
@permission_required('roles:manage')
def create_role_endpoint():
    """Super Admin creates a new custom role."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('role_name') or '').strip()
    description = (data.get('description') or '').strip()
    data_scope = (data.get('data_scope') or 'DEPARTMENT').strip().upper()
    permissions = data.get('permissions') or []

    if not name:
        return jsonify({'status': 'error', 'message': 'Role Name is required.'}), 400

    if data_scope not in ('ALL', 'DEPARTMENT', 'OWN'):
        return jsonify({'status': 'error', 'message': 'Data Scope must be one of: ALL, DEPARTMENT, OWN.'}), 400

    current_user = get_current_user()
    db = get_db()
    try:
        new_role = db.create_role(
            name=name,
            description=description,
            data_scope=data_scope,
            permissions=permissions
        )
        try:
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action='ROLE_CREATED',
                module='RBAC',
                record_id=new_role.get('Role ID') or new_role.get('role_id'),
                department=current_user.get('department'),
                details=f"Created custom role '{name}' with data scope '{data_scope}'.",
                new_state=json.dumps(new_role),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        normalized = normalize_role_dict(new_role)
        return jsonify({
            'status': 'success',
            'message': f"Role '{name}' created successfully.",
            'role': normalized
        }), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error creating role: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to create role: {str(e)}"}), 500

@app.route('/api/rbac/roles/<role_id>', methods=['PUT'])
@login_required
@permission_required('roles:manage')
def update_role_endpoint(role_id):
    """Super Admin updates an existing role configuration."""
    data = request.get_json(silent=True) or request.form or {}
    name = data.get('name') or data.get('role_name')
    description = data.get('description')
    data_scope = data.get('data_scope')
    permissions = data.get('permissions')

    current_user = get_current_user()
    db = get_db()
    try:
        old_role = db.get_role_by_id(role_id)
        if not old_role:
            return jsonify({'status': 'error', 'message': f"Role '{role_id}' not found."}), 404

        updated = db.update_role(
            role_id=role_id,
            name=name,
            description=description,
            data_scope=data_scope,
            permissions=permissions
        )
        role_display_name = updated.get('Name') or updated.get('role_name') or name or role_id
        try:
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action='ROLE_UPDATED',
                module='RBAC',
                record_id=role_id,
                department=current_user.get('department'),
                details=f"Updated role '{role_display_name}'.",
                previous_state=json.dumps(old_role),
                new_state=json.dumps(updated),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        normalized = normalize_role_dict(updated)
        return jsonify({
            'status': 'success',
            'message': f"Role '{role_display_name}' updated successfully.",
            'role': normalized
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error updating role {role_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to update role: {str(e)}"}), 500

@app.route('/api/rbac/roles/<role_id>', methods=['DELETE'])
@app.route('/api/rbac/roles/<role_id>/delete', methods=['POST'])
@login_required
@permission_required('roles:manage')
def delete_role_endpoint(role_id):
    """Super Admin deletes a non-system role after verifying no users are assigned."""
    current_user = get_current_user()
    db = get_db()
    try:
        old_role = db.get_role_by_id(role_id)
        if not old_role:
            return jsonify({'status': 'error', 'message': f"Role '{role_id}' not found."}), 404

        db.delete_role(role_id)
        try:
            db.log_audit_event(
                user_id=current_user.get('user_id'),
                user_email=current_user.get('email'),
                user_role=current_user.get('role'),
                action='ROLE_DELETED',
                module='RBAC',
                record_id=role_id,
                department=current_user.get('department'),
                details=f"Deleted custom role '{old_role.get('Name')}'.",
                previous_state=json.dumps(old_role),
                result='SUCCESS',
                ip_address=request.remote_addr or ''
            )
        except Exception as audit_err:
            logger.warning(f"Could not record audit log: {audit_err}")

        return jsonify({
            'status': 'success',
            'message': f"Role '{old_role.get('Name')}' deleted successfully."
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 409
    except Exception as e:
        logger.error(f"Error deleting role {role_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete role: {str(e)}"}), 500

@app.route('/api/rbac/audit-logs', methods=['GET'])
@login_required
@permission_required('audit_logs:view')
def get_audit_logs_endpoint():
    """Super Admin reviews security and operation audit trail logs."""
    limit = int(request.args.get('limit', 100))
    module = request.args.get('module')
    action = request.args.get('action')
    user_email = request.args.get('user_email')

    db = get_db()
    try:
        logs = db.get_audit_logs(limit=limit, module=module, action=action, user_email=user_email)
        return jsonify({
            'status': 'success',
            'logs': logs,
            'count': len(logs)
        })
    except Exception as e:
        logger.error(f"Error fetching audit logs: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to fetch audit logs: {str(e)}"}), 500

@app.route('/api/rbac/audit-logs/export', methods=['GET'])
@login_required
@permission_required('audit_logs:export')
def export_audit_logs_csv():
    """Exports audit logs to CSV."""
    db = get_db()
    try:
        logs = db.get_audit_logs(limit=1000)
        output = io.StringIO()
        fieldnames = ['Log ID', 'Timestamp', 'User Email', 'User Role', 'Action', 'Module', 'Record ID', 'Department', 'Details', 'Result', 'IP Address']
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for l in logs:
            writer.writerow(l)

        csv_bytes = output.getvalue().encode('utf-8')
        timestamp_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        return send_file(
            io.BytesIO(csv_bytes),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"audit_logs_{timestamp_str}.csv"
        )
    except Exception as e:
        logger.error(f"Error exporting audit logs: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to export audit logs: {str(e)}"}), 500

# =============================================================================
# REPORTS & ANALYTICS APIS
# =============================================================================

def _extract_report_filters(req_source):
    """Helper to extract standardized report filter dictionary from request args or body."""
    filters = {}
    # Direct scalar fields
    for k in [
        'date_preset', 'start_date', 'end_date', 'month_year', 'year', 'employee_name', 'handled_by',
        'salesperson_name', 'complain_types', 'category', 'customer_name',
        'customer_mobile_no', 'invoice_no', 'closing_start_date', 'closing_end_date',
        'gr_expected_start_date', 'gr_expected_end_date', 'gr_amount_min', 'gr_amount_max',
        'overdue_only', 'search', 'from_department', 'from_dept'
    ]:
        val = req_source.get(k)
        if val is not None and str(val).strip() != '':
            filters[k] = str(val).strip()

    # List / Multi-value fields (support either list object, getlist, or comma-separated string)
    for list_key in ['departments', 'vendors', 'statuses', 'priorities']:
        val = req_source.get(list_key)
        if hasattr(req_source, 'getlist'):
            multi = req_source.getlist(list_key)
            if len(multi) > 1:
                val = multi
        if isinstance(val, (list, tuple)):
            filters[list_key] = [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str) and val.strip():
            filters[list_key] = [p.strip() for p in val.split(',') if p.strip()]

    return filters

@app.route('/api/reports', methods=['GET', 'POST'])
@login_required
@permission_required('reports:view')
def get_reports_data():
    """
    Returns filtered complaint records, analytical aggregations, and KPI summaries.
    Strictly applies Role-Based Access Control (RBAC):
    - Super Admin & Admin: Access all records across all departments.
    - Support Staff: Access records for their assigned department or handled by them.
    - User: Access only their own submitted complaints.
    """
    user = get_current_user()
    db = get_db()

    req_source = request.get_json(silent=True) or request.args
    filters = _extract_report_filters(req_source)

    sort_by = req_source.get('sort_by', 'Complain Date')
    sort_dir = req_source.get('sort_dir', 'desc')
    page = int(req_source.get('page', 1))
    page_size = req_source.get('page_size', 25)

    try:
        # 1. Fetch complaints from persistent Google Sheets database with department scoping
        all_complaints = db.get_complaints_for_user(
            user['email'],
            user['role'],
            user_departments=user.get('departments'),
            user_dict=user
        )

        # 2. Enforce RBAC security
        rbac_complaints = ReportService.filter_by_rbac(all_complaints, user)

        # 3. Apply active report filters
        filtered_records = ReportService.apply_filters(rbac_complaints, filters)

        # 4. Compute dynamic aggregations & KPIs
        aggregations = ReportService.calculate_aggregations(filtered_records)

        # 5. Sort & Paginate records
        paged_records, cur_page, total_pages, total_records = ReportService.sort_and_paginate(
            filtered_records,
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size
        )

        return jsonify({
            'status': 'success',
            'records': paged_records,
            'pagination': {
                'page': cur_page,
                'total_pages': total_pages,
                'total_records': total_records,
                'page_size': page_size
            },
            'kpis': aggregations['kpis'],
            'status_breakdown': aggregations['status_breakdown'],
            'priority_breakdown': aggregations['priority_breakdown'],
            'department_breakdown': aggregations['department_breakdown'],
            'top_vendors': aggregations['top_vendors'],
            'monthly_trend': aggregations['monthly_trend'],
            'employee_performance': aggregations['employee_performance'],
            'available_columns': ALL_REPORT_COLUMNS
        })
    except Exception as e:
        logger.error(f"Error compiling reports data: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Failed to generate report: {str(e)}"}), 500

@app.route('/api/reports/export/<export_format>', methods=['GET', 'POST'])
@login_required
@permission_required('reports:view')
def export_reports(export_format):
    """
    Exports live filtered complaint records in Excel (.xlsx), CSV (.csv), or PDF (.pdf) format.
    Streams binary file download directly to the client browser.
    """
    user = get_current_user()
    db = get_db()

    fmt = str(export_format).lower().strip()
    if fmt not in ('excel', 'xlsx', 'csv', 'pdf'):
        return jsonify({'status': 'error', 'message': f"Unsupported export format '{export_format}'."}), 400

    export_perm_map = {
        'excel': 'reports:export_excel',
        'xlsx': 'reports:export_excel',
        'csv': 'reports:export_csv',
        'pdf': 'reports:export_pdf'
    }
    required_export_perm = export_perm_map.get(fmt)
    if required_export_perm and not RBACService.has_permission(user, required_export_perm):
        return jsonify({
            'status': 'error',
            'message': f"Access forbidden. Permission '{required_export_perm}' required to export reports in {fmt.upper()} format."
        }), 403

    if request.method == 'POST':
        req_source = request.get_json(silent=True) or request.form
    else:
        req_source = request.args

    filters = _extract_report_filters(req_source)
    sort_by = req_source.get('sort_by', 'Complain Date')
    sort_dir = req_source.get('sort_dir', 'desc')
    report_title = (req_source.get('report_title') or 'Customer Complaint Management Report').strip()

    # Resolve requested columns
    requested_cols = req_source.get('columns')
    if isinstance(requested_cols, str):
        requested_cols = [c.strip() for c in requested_cols.split(',') if c.strip()]
    elif not isinstance(requested_cols, (list, tuple)):
        requested_cols = None

    if requested_cols:
        resolved_columns = [COLUMN_MAP[c] for c in requested_cols if c in COLUMN_MAP]
    else:
        resolved_columns = [COLUMN_MAP[c] for c in DEFAULT_COL_IDS if c in COLUMN_MAP]

    if not resolved_columns:
        resolved_columns = [col for col in ALL_REPORT_COLUMNS if col['default']]

    try:
        # 1. Fetch complaints from persistent Google Sheets database with department scoping
        all_complaints = db.get_complaints_for_user(
            user['email'],
            user['role'],
            user_departments=user.get('departments'),
            user_dict=user
        )

        # 2. RBAC filter
        rbac_complaints = ReportService.filter_by_rbac(all_complaints, user)

        # 3. Apply active filters
        filtered_records = ReportService.apply_filters(rbac_complaints, filters)

        # 4. Sort all filtered records
        sorted_records, _, _, _ = ReportService.sort_and_paginate(
            filtered_records,
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=1,
            page_size='all'
        )

        # Build human-readable filter summary string
        summary_parts = []
        if filters.get('date_preset') and filters['date_preset'] != 'all':
            summary_parts.append(f"Date Range: {filters['date_preset'].replace('_', ' ').title()}")
        elif filters.get('start_date') or filters.get('end_date'):
            summary_parts.append(f"Date: {filters.get('start_date', 'Start')} to {filters.get('end_date', 'Present')}")
        if filters.get('departments'):
            summary_parts.append(f"Dept: {', '.join(filters['departments'])}")
        if filters.get('vendors'):
            summary_parts.append(f"Vendor: {', '.join(filters['vendors'])}")
        if filters.get('statuses'):
            summary_parts.append(f"Status: {', '.join(filters['statuses'])}")
        if filters.get('priorities'):
            summary_parts.append(f"Priority: {', '.join(filters['priorities'])}")
        if filters.get('overdue_only'):
            summary_parts.append("Overdue Only")
        filter_summary = " | ".join(summary_parts) if summary_parts else "All Filtered Records"

        timestamp_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

        # 5. Generate formatted export binary
        if fmt in ('excel', 'xlsx'):
            excel_bytes = ReportService.generate_excel(
                records=sorted_records,
                columns=resolved_columns,
                report_title=report_title,
                filter_summary=filter_summary,
                user_name=user['name']
            )
            return send_file(
                io.BytesIO(excel_bytes),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f"complaints_report_{timestamp_str}.xlsx"
            )

        elif fmt == 'csv':
            csv_bytes = ReportService.generate_csv(
                records=sorted_records,
                columns=resolved_columns,
                report_title=report_title,
                filter_summary=filter_summary,
                user_name=user['name']
            )
            return send_file(
                io.BytesIO(csv_bytes),
                mimetype='text/csv',
                as_attachment=True,
                download_name=f"complaints_report_{timestamp_str}.csv"
            )

        elif fmt == 'pdf':
            aggregations = ReportService.calculate_aggregations(sorted_records)
            pdf_bytes = ReportService.generate_pdf(
                records=sorted_records,
                columns=resolved_columns,
                report_title=report_title,
                filter_summary=filter_summary,
                user_name=user['name'],
                kpis=aggregations['kpis']
            )
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f"complaints_report_{timestamp_str}.pdf"
            )

    except Exception as e:
        logger.error(f"Error generating export ({fmt}): {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f"Export failed: {str(e)}"}), 500

if __name__ == '__main__':
    import socket
    port = int(os.environ.get('PORT', 5050))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'

    # Configured local Wi-Fi / LAN IP address
    lan_ip = os.environ.get('LAN_IP') or '192.168.1.55'

    print("=" * 72)
    print("  Customer Complaint Management System — Office LAN Access Active")
    print(f"  • Local Loopback:     http://127.0.0.1:{port}")
    print(f"  • Localhost:          http://localhost:{port}")
    print(f"  • Office LAN Wi-Fi:   http://{lan_ip}:{port}")
    print(f"  • Network Interface:  0.0.0.0:{port} (Private Network)")
    print("=" * 72)

    use_waitress = os.environ.get('USE_WAITRESS', 'True').lower() == 'true' and not debug_mode
    if use_waitress:
        try:
            from waitress import serve
            print(f"Serving via Waitress production WSGI server on 0.0.0.0:{port} (threads=6, debug=False)...")
            serve(app, host='0.0.0.0', port=port, threads=6)
        except ImportError:
            print(f"Waitress not found; falling back to Flask server on 0.0.0.0:{port} (debug={debug_mode})...")
            app.run(host='0.0.0.0', port=port, debug=debug_mode)
    else:
        app.run(host='0.0.0.0', port=port, debug=debug_mode)
