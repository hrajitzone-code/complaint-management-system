from functools import wraps
from flask import session, jsonify, request
from werkzeug.security import check_password_hash
from services.rbac_service import (
    RBACService,
    DEFAULT_ROLE_TEMPLATES,
    ALL_PERMISSION_IDS
)

def get_current_user():
    """Returns the authenticated user dict from Flask session."""
    return session.get('user')

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
        if str(u.get('is_active', 'TRUE')).upper() not in ('TRUE', '1', 'YES'):
            session.clear()
            return jsonify({'status': 'error', 'message': 'Account is disabled or archived. Please contact your administrator.'}), 403
        return f(*args, **kwargs)
    return decorated

def staff_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
        if str(u.get('is_active', 'TRUE')).upper() not in ('TRUE', '1', 'YES'):
            session.clear()
            return jsonify({'status': 'error', 'message': 'Account is disabled or archived. Please contact your administrator.'}), 403
        canon_role = RBACService.canonical_role(u.get('role'))
        if canon_role not in ('Super Admin', 'Admin', 'Department Head', 'Department Staff'):
            return jsonify({'status': 'error', 'message': 'Access forbidden. Staff or Admin privileges required.'}), 403
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
        if str(u.get('is_active', 'TRUE')).upper() not in ('TRUE', '1', 'YES'):
            session.clear()
            return jsonify({'status': 'error', 'message': 'Account is disabled or archived. Please contact your administrator.'}), 403
        canon_role = RBACService.canonical_role(u.get('role'))
        if canon_role not in ('Super Admin', 'Admin'):
            return jsonify({'status': 'error', 'message': 'Access forbidden. Administrator privileges required.'}), 403
        return f(*args, **kwargs)
    return decorated

def super_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
        if str(u.get('is_active', 'TRUE')).upper() not in ('TRUE', '1', 'YES'):
            session.clear()
            return jsonify({'status': 'error', 'message': 'Account is disabled or archived. Please contact your administrator.'}), 403
        canon_role = RBACService.canonical_role(u.get('role'))
        if canon_role != 'Super Admin':
            return jsonify({'status': 'error', 'message': 'Access forbidden. Super Administrator privileges required.'}), 403
        return f(*args, **kwargs)
    return decorated

def permission_required(permission_id):
    """
    Enforces that the authenticated user holds the required granular permission.
    Default-deny authorization.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            u = get_current_user()
            if not u:
                return jsonify({'status': 'error', 'message': 'Authentication required. Please login.'}), 401
            if str(u.get('is_active', 'TRUE')).upper() not in ('TRUE', '1', 'YES'):
                session.clear()
                return jsonify({'status': 'error', 'message': 'Account is disabled or archived.'}), 403

            canon_role = RBACService.canonical_role(u.get('role'))
            if canon_role == 'Super Admin':
                return f(*args, **kwargs)

            user_perms = set(u.get('permissions') or [])
            if permission_id not in user_perms:
                if not RBACService.has_permission(u, permission_id):
                    return jsonify({
                        'status': 'error',
                        'message': f"Access forbidden. Required permission: '{permission_id}'."
                    }), 403

            return f(*args, **kwargs)
        return decorated
    return decorator

def record_access_required(action='view', get_db_fn=None):
    """
    Enforces department-level data scoping and record-level authorization for complaints.
    Prevents IDOR / cross-department direct URL access.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            u = get_current_user()
            if not u:
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            if str(u.get('is_active', 'TRUE')).upper() not in ('TRUE', '1', 'YES'):
                session.clear()
                return jsonify({'status': 'error', 'message': 'Account is disabled or archived.'}), 403

            complaint_id = kwargs.get('complaint_id') or (request.view_args.get('complaint_id') if request.view_args else None)
            if not complaint_id:
                complaint_id = request.args.get('complaint_id') or (request.get_json(silent=True) or {}).get('complaint_id')

            if complaint_id and get_db_fn:
                db = get_db_fn()
                complaint = db.get_complaint_by_id(complaint_id)
                if not complaint:
                    return jsonify({'status': 'error', 'message': f"Complaint '{complaint_id}' not found."}), 404

                try:
                    custom_roles = db.get_all_roles()
                except Exception:
                    custom_roles = None

                allowed, reason = RBACService.can_access_complaint(u, complaint, action=action, custom_roles=custom_roles)
                if not allowed:
                    return jsonify({'status': 'error', 'message': f"Access forbidden: {reason}"}), 403

            return f(*args, **kwargs)
        return decorated
    return decorator

def verify_and_login_user(email, password, db_engine):
    """
    Verifies user credentials against the Google Sheets Users database and sets the session.
    Computes effective permissions and department scope.
    Returns (success: bool, user_data: dict, error_message: str)
    """
    if not email or not password:
        return False, None, "Email and Password are required."

    clean_email = email.lower().strip()
    user_record = db_engine.find_user_by_email(clean_email)

    if not user_record:
        return False, None, "Invalid email or password."

    if str(user_record.get('Is Active', '')).upper() != 'TRUE':
        if str(user_record.get('Is Active', '')).upper() == 'ARCHIVED':
            return False, None, "Account has been archived. Please contact your administrator."
        return False, None, "Account is disabled. Please contact your administrator."

    stored_hash = user_record.get('Password Hash', '')
    if not stored_hash or not check_password_hash(stored_hash, password):
        return False, None, "Invalid email or password."

    # Update last login
    try:
        db_engine.update_user_last_login(clean_email)
    except Exception:
        pass

    canon_role = RBACService.canonical_role(user_record.get('Role', 'User'))
    try:
        custom_roles = db_engine.get_all_roles()
    except Exception:
        custom_roles = None

    effective_perms = list(sorted(RBACService.get_effective_permissions(user_record, custom_roles=custom_roles)))
    departments = RBACService.get_user_departments(user_record)
    role_cfg = RBACService.get_role_template(canon_role, custom_roles=custom_roles)
    data_scope = role_cfg.get('data_scope', 'DEPARTMENT') if role_cfg else 'DEPARTMENT'

    user_data = {
        'user_id': user_record.get('User ID'),
        'name': user_record.get('Full Name'),
        'email': user_record.get('Email'),
        'role': canon_role,
        'department': user_record.get('Department', 'General'),
        'departments': departments,
        'permissions': effective_perms,
        'data_scope': data_scope,
        'custom_permissions': user_record.get('Custom Permissions', ''),
        'is_active': str(user_record.get('Is Active', 'TRUE')).upper()
    }

    session.permanent = True
    session['user'] = user_data
    return True, user_data, None

def refresh_user_session(user_id_or_email, db_engine):
    """
    Refreshes the permissions and department scope stored in session for the active user.
    """
    u = get_current_user()
    if not u:
        return None
    user_record = db_engine.find_user_by_email(user_id_or_email) or db_engine.find_user_by_id(user_id_or_email)
    if not user_record:
        return None

    canon_role = RBACService.canonical_role(user_record.get('Role', 'User'))
    try:
        custom_roles = db_engine.get_all_roles()
    except Exception:
        custom_roles = None

    effective_perms = list(sorted(RBACService.get_effective_permissions(user_record, custom_roles=custom_roles)))
    departments = RBACService.get_user_departments(user_record)
    role_cfg = RBACService.get_role_template(canon_role, custom_roles=custom_roles)
    data_scope = role_cfg.get('data_scope', 'DEPARTMENT') if role_cfg else 'DEPARTMENT'

    user_data = {
        'user_id': user_record.get('User ID'),
        'name': user_record.get('Full Name'),
        'email': user_record.get('Email'),
        'role': canon_role,
        'department': user_record.get('Department', 'General'),
        'departments': departments,
        'permissions': effective_perms,
        'data_scope': data_scope,
        'custom_permissions': user_record.get('Custom Permissions', ''),
        'is_active': str(user_record.get('Is Active', 'TRUE')).upper()
    }
    session['user'] = user_data
    return user_data
