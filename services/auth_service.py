from functools import wraps
from flask import session, jsonify, request
from werkzeug.security import check_password_hash

def get_current_user():
    """Returns the authenticated user dict from Flask session."""
    return session.get('user')

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
        return f(*args, **kwargs)
    return decorated

def staff_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
        if u.get('role') not in ('Super Admin', 'Admin', 'Support Staff'):
            return jsonify({'status': 'error', 'message': 'Access forbidden. Staff or Admin privileges required.'}), 403
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
        if u.get('role') not in ('Super Admin', 'Admin'):
            return jsonify({'status': 'error', 'message': 'Access forbidden. Administrator privileges required.'}), 403
        return f(*args, **kwargs)
    return decorated

def verify_and_login_user(email, password, db_engine):
    """
    Verifies user credentials against the Google Sheets Users database and sets the session.
    Returns (success: bool, user_data: dict, error_message: str)
    """
    if not email or not password:
        return False, None, "Email and Password are required."

    clean_email = email.lower().strip()
    user_record = db_engine.find_user_by_email(clean_email)

    if not user_record:
        return False, None, "Invalid email or password."

    if str(user_record.get('Is Active', '')).upper() != 'TRUE':
        return False, None, "Account is disabled. Please contact your administrator."

    stored_hash = user_record.get('Password Hash', '')
    if not stored_hash or not check_password_hash(stored_hash, password):
        return False, None, "Invalid email or password."

    # Update last login
    try:
        db_engine.update_user_last_login(clean_email)
    except Exception:
        pass

    user_data = {
        'user_id': user_record.get('User ID'),
        'name': user_record.get('Full Name'),
        'email': user_record.get('Email'),
        'role': user_record.get('Role', 'User'),
        'department': user_record.get('Department', 'General')
    }

    session.permanent = True
    session['user'] = user_data
    return True, user_data, None
