import os
import io
import time
import secrets
import urllib.parse
import datetime
import logging
import threading
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, send_file
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
    verify_and_login_user
)
from services.report_service import (
    ReportService,
    ALL_REPORT_COLUMNS,
    DEFAULT_COL_IDS,
    COLUMN_MAP
)

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
        return os.environ.get('OAUTH_REDIRECT_URI').strip()
    if os.environ.get('VERCEL') == '1' or os.environ.get('VERCEL_URL'):
        v_url = os.environ.get('VERCEL_URL', '').strip().rstrip('/')
        if v_url:
            if not v_url.startswith('http'):
                v_url = f"https://{v_url}"
            return f"{v_url}/api/drive/oauth2callback"
    proto = request.headers.get('X-Forwarded-Proto', request.scheme)
    host = request.headers.get('X-Forwarded-Host', request.host)
    return f"{proto}://{host}/api/drive/oauth2callback"

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

        db = get_db()
        # Stored encrypted in System_Config via SheetsDB
        if refresh_token:
            db.set_system_config('google_user_refresh_token', refresh_token, updated_by='oauth_callback')
        if access_token:
            db.set_system_config('google_user_access_token', access_token, updated_by='oauth_callback')

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
    """Terminates session."""
    session.clear()
    return jsonify({'status': 'success', 'message': 'Logged out successfully.'})

@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    """Returns currently authenticated user."""
    u = get_current_user()
    if not u:
        return jsonify({'status': 'error', 'message': 'Not authenticated.'}), 401
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
@admin_required
def create_department():
    """Admin creates a new complaint department."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    lead_email = (data.get('lead_email') or '').strip()

    if not name:
        return jsonify({'status': 'error', 'message': 'Department name is required.'}), 400

    db = get_db()
    try:
        dept = db.create_department(name=name, description=description, lead_email=lead_email)
        return jsonify({'status': 'success', 'message': f"Department '{name}' created.", 'department': dept}), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error creating department: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to create department: {str(e)}"}), 500

@app.route('/api/departments/<dept_id>', methods=['PUT'])
@admin_required
def update_department_endpoint(dept_id):
    """Admin updates department details."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or '').strip()
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
@admin_required
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
# COMPLAINTS APIS
# =============================================================================

@app.route('/api/complaints', methods=['POST'])
@login_required
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

    # Form Validation
    if not customer_name:
        return jsonify({'status': 'error', 'message': 'Customer Name is required.'}), 400
    if not department_name:
        return jsonify({'status': 'error', 'message': 'Department Name is required.'}), 400
    if not complain_types:
        return jsonify({'status': 'error', 'message': 'Complain Types is required.'}), 400
    if not complain_details:
        return jsonify({'status': 'error', 'message': 'Complain Details is required.'}), 400

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
            complainant_email=user['email']
        )

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
    Permissions:
    - Super Admin, Admin, and Support Staff can edit all complaints.
    - Regular Users can only edit their own complaints if Status is 'Submitted'.
    """
    user = get_current_user()
    db = get_db()
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    is_staff_or_admin = user['role'] in ('Super Admin', 'Admin', 'Support Staff')
    is_owner = c.get('Complainant Email', '').lower().strip() == user['email'].lower().strip()

    if not is_staff_or_admin:
        if not is_owner:
            return jsonify({'status': 'error', 'message': 'Access forbidden. You cannot edit complaints lodged by other users.'}), 403
        if c.get('Status') != 'Submitted':
            return jsonify({
                'status': 'error',
                'message': f"Cannot edit complaint in '{c.get('Status')}' status. Only 'Submitted' complaints can be edited by regular users."
            }), 403

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

    try:
        updated = db.update_complaint(complaint_id, raw_data, user)
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
@admin_required
def delete_complaint_endpoint(complaint_id):
    """
    Deletes a complaint permanently from Google Sheets.
    Restricted to Super Admin and Admin roles only.
    Records an audit log entry in Status History before row removal.
    """
    user = get_current_user()
    db = get_db()
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    try:
        db.delete_complaint(complaint_id, user)
        return jsonify({
            'status': 'success',
            'message': f"Complaint {complaint_id} has been permanently deleted and logged to the audit trail."
        })
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 404
    except Exception as e:
        logger.error(f"Error deleting complaint {complaint_id}: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete complaint from Google Sheets: {str(e)}"}), 500

@app.route('/api/complaints', methods=['GET'])
@login_required
def list_complaints():
    """
    Returns complaints filtered by user access:
    - Regular Users: strictly only their own complaints.
    - Staff / Admins: all complaints.
    """
    user = get_current_user()
    db = get_db()
    try:
        complaints = db.get_complaints_for_user(user['email'], user['role'])
        return jsonify({
            'status': 'success',
            'complaints': complaints,
            'count': len(complaints)
        })
    except Exception as e:
        logger.error(f"Error listing complaints: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to read complaints: {str(e)}"}), 500

@app.route('/api/complaints/<complaint_id>', methods=['GET'])
@login_required
def get_complaint_details(complaint_id):
    """
    Retrieves full details, status timeline, and comments for a single complaint.
    Strictly verifies ownership for regular users (prevents cross-user IDOR access).
    """
    user = get_current_user()
    db = get_db()
    try:
        c = db.get_complaint_by_id(complaint_id)
        if not c:
            return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

        # Access check: Regular users can ONLY view their own complaints
        if user['role'] == 'User' and c.get('Complainant Email', '').lower().strip() != user['email'].lower().strip():
            return jsonify({'status': 'error', 'message': 'Access forbidden. You do not have permission to view this complaint.'}), 403

        history = db.get_status_history(complaint_id)
        comments = db.get_comments(complaint_id, user_role=user['role'])

        return jsonify({
            'status': 'success',
            'complaint': c,
            'history': history,
            'comments': comments
        })
    except Exception as e:
        logger.error(f"Error fetching complaint details: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to read complaint details: {str(e)}"}), 500

@app.route('/api/complaints/<complaint_id>/status', methods=['POST'])
@staff_required
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
    try:
        db.update_complaint_status(
            complaint_id=complaint_id,
            new_status=new_status,
            changed_by_user=user,
            remarks=remarks
        )
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
@staff_required
def assign_complaint(complaint_id):
    """Staff/Admin assigns a complaint to a support staff member."""
    user = get_current_user()
    data = request.get_json(silent=True) or request.form or {}
    assigned_to_email = (data.get('assigned_to_email') or '').strip().lower()
    assigned_to_name = (data.get('assigned_to_name') or '').strip()

    if not assigned_to_email:
        return jsonify({'status': 'error', 'message': 'Assignee email is required.'}), 400

    db = get_db()
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

    # Regular users can only make Public Updates
    if user['role'] == 'User':
        comment_type = 'Public Update'

    db = get_db()
    # Verify complaint exists and user has permission to comment
    c = db.get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

    if user['role'] == 'User' and c.get('Complainant Email', '').lower().strip() != user['email'].lower().strip():
        return jsonify({'status': 'error', 'message': 'Access forbidden.'}), 403

    try:
        new_comment = db.add_comment(
            complaint_id=complaint_id,
            author_name=user['name'],
            author_email=user['email'],
            author_role=user['role'],
            comment_type=comment_type,
            message=message
        )
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
@admin_required
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
@admin_required
def admin_create_user():
    """Admin creates a staff member or user."""
    data = request.get_json(silent=True) or request.form or {}
    full_name = (data.get('full_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()
    role = (data.get('role') or 'Support Staff').strip()
    department = (data.get('department') or 'General').strip()

    valid_roles = ('Super Admin', 'Admin', 'Support Staff', 'User')
    if role not in valid_roles:
        return jsonify({'status': 'error', 'message': f"Invalid role. Allowed roles: {', '.join(valid_roles)}"}), 400

    if not full_name or not email or not password:
        return jsonify({'status': 'error', 'message': 'Full name, email, and password are required.'}), 400

    db = get_db()
    try:
        user = db.create_user(full_name=full_name, email=email, password=password, role=role, department=department)
        return jsonify({'status': 'success', 'message': f"User '{full_name}' created.", 'user': user}), 201
    except ValueError as val_err:
        return jsonify({'status': 'error', 'message': str(val_err)}), 400
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to create user: {str(e)}"}), 500

# =============================================================================
# REPORTS & ANALYTICS APIS
# =============================================================================

def _extract_report_filters(req_source):
    """Helper to extract standardized report filter dictionary from request args or body."""
    filters = {}
    # Direct scalar fields
    for k in [
        'date_preset', 'start_date', 'end_date', 'employee_name', 'handled_by',
        'salesperson_name', 'complain_types', 'category', 'customer_name',
        'customer_mobile_no', 'invoice_no', 'closing_start_date', 'closing_end_date',
        'gr_expected_start_date', 'gr_expected_end_date', 'gr_amount_min', 'gr_amount_max',
        'overdue_only', 'search'
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
        # 1. Fetch complaints from persistent Google Sheets database
        all_complaints = db.get_complaints_for_user(user['email'], user['role'])

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
        # 1. Fetch complaints from persistent Google Sheets database
        all_complaints = db.get_complaints_for_user(user['email'], user['role'])

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
                mimetype='text/csv; charset=utf-8',
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
