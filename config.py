import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

class Config:
    # Flask Session
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'complaint_system_secure_secret_key_2026_x8f9a2b'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true' or os.environ.get('VERCEL') == '1'
    PERMANENT_SESSION_LIFETIME_HOURS = 24

    # Google API Config
    GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
        'GOOGLE_SERVICE_ACCOUNT_FILE',
        os.path.join(BASE_DIR, 'config', 'google_service_account.json')
    )
    GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')

    # Optional User OAuth Credentials (uses human storage quota instead of 0-byte service account)
    GOOGLE_USER_OAUTH_FILE = os.environ.get(
        'GOOGLE_USER_OAUTH_FILE',
        os.path.join(BASE_DIR, 'config', 'google_user_oauth.json')
    )
    GOOGLE_USER_CLIENT_ID = os.environ.get('GOOGLE_USER_CLIENT_ID')
    GOOGLE_USER_CLIENT_SECRET = os.environ.get('GOOGLE_USER_CLIENT_SECRET')
    GOOGLE_USER_REFRESH_TOKEN = os.environ.get('GOOGLE_USER_REFRESH_TOKEN')

    # Sensitive Config Encryption Key (for System_Config in Google Sheets, compatible with Vercel)
    CONFIG_ENCRYPTION_KEY = os.environ.get('CONFIG_ENCRYPTION_KEY') or os.environ.get('ENCRYPTION_KEY')
    
    # Target Google Drive Folder specified by user
    GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '19OyFQPQAOPexKT8IqAkHN2Z8Z3U_n4Aq')
    GOOGLE_DRIVE_FOLDER_URL = f"https://drive.google.com/drive/folders/{GOOGLE_DRIVE_FOLDER_ID}"
    
    # Central Spreadsheet DB Name
    SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME', 'Complaint_Management_System_DB')
    
    # Optional direct spreadsheet ID override (e.g. if pre-created or already shared)
    COMPLAINTS_SPREADSHEET_ID = os.environ.get('COMPLAINTS_SPREADSHEET_ID', '1Zp-8bCef5eEjA2F_fMiduNfF-evzxCe0K1dTn8Kp23Y')

    # Initial Admin Provisioning (only provisioned automatically if INITIAL_ADMIN_PASSWORD is set in .env)
    INITIAL_ADMIN_EMAIL = os.environ.get('INITIAL_ADMIN_EMAIL', 'admin@complaints.local').lower().strip()
    INITIAL_ADMIN_NAME = os.environ.get('INITIAL_ADMIN_NAME', 'System Administrator')
    INITIAL_ADMIN_PASSWORD = os.environ.get('INITIAL_ADMIN_PASSWORD')  # No hardcoded default; requires .env or setup UI

    # Attachment limits
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp', 'doc', 'docx', 'txt', 'csv', 'xlsx'}
    
    # Google API Scopes
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
