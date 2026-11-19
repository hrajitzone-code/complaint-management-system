import os
import re
import json
import time
import uuid
import logging
import datetime
import threading
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from services.google_client import (
    get_gspread_client,
    get_google_credentials,
    check_drive_folder_access,
    sanitize_row,
    sanitize_cell_value
)
from services.crypto_utils import (
    is_sensitive_key,
    encrypt_secret,
    decrypt_secret,
    mask_secret
)
from services.rbac_service import (
    RBACService,
    DEFAULT_ROLE_TEMPLATES,
    ALL_PERMISSION_IDS
)

logger = logging.getLogger(__name__)

def execute_with_retry(fn, max_retries=5, initial_delay=2.0, backoff_factor=2.0):
    """
    Executes a Google Sheets API operation with exponential backoff on transient errors.
    """
    delay = initial_delay
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            is_transient = any(tok in err_str for tok in [
                '429', 'quota', 'rate limit', '500', '502', '503', 'timed out', 'connection reset', 'transport error'
            ])
            if not is_transient or attempt == max_retries:
                raise e
            wait_time = max(delay, 5.0) if ('quota' in err_str or '429' in err_str) else delay
            logger.warning(f"Google API transient/quota error ({e}), backing off {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})...")
            time.sleep(wait_time)
            delay = wait_time * backoff_factor
    raise last_err

# Canonical Worksheets and their column schemas
SHEET_SCHEMAS = {
    'Complaints': [
        'Complaint ID', 'Timestamp', 'Complainant Email', 'Employee Name',
        'Complain Date', 'Customer Name', 'Invoice No.', 'Complain Types',
        'Priority', 'Remarks', 'Complain Details', 'Customer Mobile No.',
        'Department Name', 'Category', 'Vendor Name', 'Salesperson Name',
        'Status', 'Closing Date', 'Solution Given', 'Handled By',
        'Total Time Taken', 'Comments', 'GR Amount', 'GR Expected Date',
        'Attachment Link', 'Last Updated At', 'From Department'
    ],
    'Users': [
        'User ID', 'Full Name', 'Email', 'Password Hash', 'Role',
        'Department', 'Is Active', 'Created At', 'Last Login', 'Custom Permissions'
    ],
    'Roles': [
        'Role ID', 'Name', 'Description', 'Data Scope', 'Permissions', 'Is System Role', 'Created At', 'Last Updated At'
    ],
    'Departments': [
        'Department ID', 'Name', 'Description', 'Lead Email', 'Is Active', 'Created At'
    ],
    'Complaint_Types': [
        'Type ID', 'Name', 'Description', 'Is Active', 'Created At'
    ],
    'Salespersons': [
        'Salesperson ID', 'Name', 'Phone', 'Email', 'Is Active', 'Created At'
    ],
    'Status History': [
        'History ID', 'Complaint ID', 'Timestamp', 'Old Status', 'New Status',
        'Changed By Name', 'Changed By Email', 'Remarks'
    ],
    'Assignments and Comments': [
        'Comment ID', 'Complaint ID', 'Timestamp', 'Author Name',
        'Author Email', 'Author Role', 'Comment Type', 'Message'
    ],
    'Audit_Logs': [
        'Log ID', 'User ID', 'User Email', 'User Role', 'Action', 'Module', 'Record ID',
        'Department', 'Details', 'Previous State', 'New State', 'Result', 'IP Address', 'Timestamp'
    ],
    'System_Config': [
        'Key', 'Value', 'Updated_At', 'Updated_By'
    ]
}

DEFAULT_DEPARTMENTS = [
    ('IT & Technical Support', 'Hardware, software, network, email, and systems assistance.'),
    ('Facilities & Infrastructure', 'Building maintenance, electrical, plumbing, hygiene, and utilities.'),
    ('Finance & Billing', 'Salary, invoice processing, reimbursement, and billing inquiries.'),
    ('Human Resources', 'Workplace culture, policy guidance, onboarding, and employee relations.'),
    ('Customer Operations', 'Customer-facing services, service delivery, and logistics complaints.')
]

class SheetsDB:
    """
    Pure Google Sheets Database abstraction.
    No SQL database dependencies. All persistent business data is managed in Google Sheets.
    """
    _spreadsheet_instance = None
    _last_init_time = 0
    _cache = {}
    _cache_ttl = 5 # seconds
    _db_lock = threading.RLock()

    @classmethod
    def get_spreadsheet(cls, force_refresh=False):
        """Connects to or locates the active Google Sheets Database."""
        if cls._spreadsheet_instance and not force_refresh:
            return cls._spreadsheet_instance

        gc = get_gspread_client()

        # 1. Try explicit spreadsheet ID from config if provided
        if Config.COMPLAINTS_SPREADSHEET_ID:
            try:
                sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)
                cls._spreadsheet_instance = sh
                cls._ensure_worksheets(sh)
                return sh
            except Exception as e:
                logger.warning(f"Could not open spreadsheet by COMPLAINTS_SPREADSHEET_ID ({Config.COMPLAINTS_SPREADSHEET_ID}): {e}")

        # 2. Try opening by exact title
        try:
            sh = gc.open(Config.SPREADSHEET_NAME)
            cls._spreadsheet_instance = sh
            cls._ensure_worksheets(sh)
            return sh
        except Exception:
            pass

        # 3. Create spreadsheet in target Google Drive Folder if accessible
        has_access, folder_info = check_drive_folder_access(Config.GOOGLE_DRIVE_FOLDER_ID)
        if has_access and folder_info.get('can_add_children', True):
            try:
                sh = gc.create(Config.SPREADSHEET_NAME, folder_id=Config.GOOGLE_DRIVE_FOLDER_ID)
                cls._spreadsheet_instance = sh
                cls._ensure_worksheets(sh)
                return sh
            except Exception as create_err:
                logger.error(f"Failed to create spreadsheet in Drive folder: {create_err}")

        # 4. Fallback: create in service account drive (if quota allows)
        try:
            sh = gc.create(Config.SPREADSHEET_NAME)
            cls._spreadsheet_instance = sh
            cls._ensure_worksheets(sh)
            return sh
        except Exception as e:
            raise RuntimeError(
                f"Cannot initialize Google Sheets database: {str(e)}. "
                f"Please ensure folder '{Config.GOOGLE_DRIVE_FOLDER_ID}' is shared with "
                f"'{get_google_credentials().service_account_email}' as Editor, "
                f"or set COMPLAINTS_SPREADSHEET_ID in .env."
            )

    @classmethod
    def _ensure_worksheets(cls, sh):
        """Guarantees that all required sheets and canonical headers exist."""
        existing_sheets = {ws.title: ws for ws in sh.worksheets()}

        for title, headers in SHEET_SCHEMAS.items():
            if title not in existing_sheets:
                logger.info(f"Creating missing worksheet '{title}' with canonical headers...")
                ws = sh.add_worksheet(title=title, rows=100, cols=len(headers) + 2)
                ws.append_row(sanitize_row(headers), value_input_option='USER_ENTERED')
                existing_sheets[title] = ws
            else:
                ws = existing_sheets[title]
                first_row = ws.row_values(1)
                if not first_row:
                    logger.info(f"Adding canonical headers to empty sheet '{title}'...")
                    ws.append_row(sanitize_row(headers), value_input_option='USER_ENTERED')

        # Ensure 'From Department' column exists in Complaints worksheet header
        if 'Complaints' in existing_sheets:
            comp_ws = existing_sheets['Complaints']
            comp_first_row = comp_ws.row_values(1)
            if comp_first_row and 'From Department' not in comp_first_row:
                logger.info("Adding 'From Department' canonical header to Complaints sheet...")
                execute_with_retry(lambda: comp_ws.update_cell(1, len(comp_first_row) + 1, 'From Department'))

        # Ensure 'Custom Permissions' column exists in Users worksheet header
        if 'Users' in existing_sheets:
            users_ws = existing_sheets['Users']
            users_first_row = users_ws.row_values(1)
            if users_first_row and 'Custom Permissions' not in users_first_row:
                logger.info("Adding 'Custom Permissions' canonical header to Users sheet...")
                execute_with_retry(lambda: users_ws.update_cell(1, len(users_first_row) + 1, 'Custom Permissions'))

        # Seed initial system roles if Roles worksheet has no data rows
        if 'Roles' in existing_sheets:
            roles_ws = existing_sheets['Roles']
            roles_rows = roles_ws.get_all_values()
            if len(roles_rows) <= 1:
                now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                logger.info("Seeding initial enterprise roles in Roles worksheet...")
                for role_name in ('Super Admin', 'Admin', 'Department Head', 'Department Staff', 'Viewer', 'User'):
                    tpl = DEFAULT_ROLE_TEMPLATES[role_name]
                    perms_str = '*' if role_name == 'Super Admin' else ','.join(tpl['permissions'])
                    r_row = [
                        tpl['id'],
                        tpl['name'],
                        tpl['description'],
                        tpl['data_scope'],
                        perms_str,
                        'TRUE' if tpl['is_system_role'] else 'FALSE',
                        now_str,
                        now_str
                    ]
                    roles_ws.append_row(sanitize_row(r_row), value_input_option='USER_ENTERED')

        # Seed initial standard complaint types if Complaint_Types worksheet has no data rows
        if 'Complaint_Types' in existing_sheets:
            ct_ws = existing_sheets['Complaint_Types']
            ct_rows = ct_ws.get_all_values()
            if len(ct_rows) <= 1:
                now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                initial_types = [
                    ('CT-001', 'Damage', 'Damaged or defective merchandise upon arrival or unboxing', 'TRUE', now_str),
                    ('CT-002', 'Product Defect', 'Manufacturing defect, stitching, material or fabric flaw', 'TRUE', now_str),
                    ('CT-003', 'Wrong Item Delivered', 'Incorrect size, color, or variant sent to customer', 'TRUE', now_str),
                    ('CT-004', 'Transit Damage', 'Item damaged during courier or cargo transportation', 'TRUE', now_str),
                    ('CT-005', 'Billing Discrepancy', 'Invoice amount, discount, or tax calculation error', 'TRUE', now_str),
                    ('CT-006', 'Delivery Delay', 'Delayed shipment beyond promised delivery date', 'TRUE', now_str),
                    ('CT-007', 'Quality Issue', 'Fabric or finishing not meeting expected standards', 'TRUE', now_str),
                    ('CT-008', 'Other', 'Other customer issues or general service complaints', 'TRUE', now_str)
                ]
                for r in initial_types:
                    ct_ws.append_row(sanitize_row(list(r)), value_input_option='USER_ENTERED')

        # Seed initial salespersons if Salespersons worksheet has no data rows
        if 'Salespersons' in existing_sheets:
            sp_ws = existing_sheets['Salespersons']
            sp_rows = sp_ws.get_all_values()
            if len(sp_rows) <= 1:
                now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                initial_sp = [
                    ('SP-001', 'Vikram Rao', '+91 98765 00001', 'vikram.rao@company.local', 'TRUE', now_str),
                    ('SP-002', 'Priya Sharma', '+91 98765 00002', 'priya.sharma@company.local', 'TRUE', now_str),
                    ('SP-003', 'Rahul Verma', '+91 98765 00003', 'rahul.verma@company.local', 'TRUE', now_str),
                    ('SP-004', 'Direct Store Sales', '', '', 'TRUE', now_str)
                ]
                for r in initial_sp:
                    sp_ws.append_row(sanitize_row(list(r)), value_input_option='USER_ENTERED')

    @classmethod
    def _get_worksheet(cls, name):
        """Returns the specific worksheet instance."""
        sh = cls.get_spreadsheet()
        return execute_with_retry(lambda: sh.worksheet(name))

    @classmethod
    def _read_records(cls, sheet_name, use_cache=True):
        """Reads all rows from a sheet as a list of dicts mapped by header."""
        with cls._db_lock:
            now = time.time()
            if use_cache and sheet_name in cls._cache:
                data, cache_time = cls._cache[sheet_name]
                if now - cache_time < cls._cache_ttl:
                    return [dict(d) for d in data]

            ws = cls._get_worksheet(sheet_name)
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                cls._cache[sheet_name] = ([], now)
                return []

            headers = [h.strip() for h in rows[0]]
            records = []
            for r in rows[1:]:
                record = {}
                for i, h in enumerate(headers):
                    record[h] = r[i] if i < len(r) else ''
                records.append(record)

            cls._cache[sheet_name] = (records, now)
            return records

    @classmethod
    def _invalidate_cache(cls, sheet_name=None):
        """Clears read cache for a specific sheet or all sheets."""
        with cls._db_lock:
            if sheet_name:
                cls._cache.pop(sheet_name, None)
            else:
                cls._cache.clear()

    # =========================================================================
    # USER OPERATIONS
    # =========================================================================

    @classmethod
    def find_user_by_email(cls, email):
        """Finds a user record by email (case-insensitive)."""
        if not email:
            return None
        target = email.lower().strip()
        users = cls._read_records('Users', use_cache=True)
        for u in users:
            if u.get('Email', '').lower().strip() == target:
                return u
        return None

    @classmethod
    def find_user_by_id(cls, user_id):
        """Finds a user record by User ID."""
        if not user_id:
            return None
        users = cls._read_records('Users', use_cache=True)
        for u in users:
            if u.get('User ID', '').strip() == str(user_id).strip():
                return u
        return None

    @classmethod
    def get_all_users(cls):
        """Returns all registered users (excluding sensitive password hash)."""
        users = cls._read_records('Users', use_cache=True)
        safe_users = []
        for u in users:
            copy = dict(u)
            copy.pop('Password Hash', None)
            safe_users.append(copy)
        return safe_users

    @classmethod
    def has_admin_user(cls):
        """Checks if at least one active Admin / Super Admin exists."""
        users = cls.get_all_users()
        for u in users:
            role = u.get('Role', '')
            is_active = str(u.get('Is Active', '')).upper() == 'TRUE'
            if role in ('Super Admin', 'Admin') and is_active:
                return True
        return False

    @classmethod
    def create_initial_admin(cls, full_name, email, password):
        """Creates the initial Super Admin during first-run setup."""
        if cls.has_admin_user():
            raise ValueError("An administrator has already been provisioned. Setup is locked.")
        return cls.create_user(
            full_name=full_name,
            email=email,
            password=password,
            role='Super Admin',
            department='Management'
        )

    @classmethod
    def create_user(cls, full_name, email, password, role='User', department='General', custom_permissions=''):
        """Creates a new user with securely hashed password, department scope, and custom permissions."""
        with cls._db_lock:
            clean_email = email.lower().strip()
            if cls.find_user_by_email(clean_email):
                raise ValueError(f"User with email '{clean_email}' already exists.")

            all_roles = [r.get('Name') or r.get('role_name') for r in cls.get_all_roles() if (r.get('Name') or r.get('role_name'))]
            canon_role = RBACService.canonical_role(role)
            if canon_role not in all_roles and role.strip() not in all_roles:
                raise ValueError(f"Invalid role '{role}'. Allowed roles: {', '.join(sorted(set(all_roles)))}")

            if isinstance(department, list):
                dept_str = ', '.join(d.strip() for d in department if d.strip())
            else:
                dept_str = str(department).strip()
            if not dept_str:
                dept_str = 'General'

            if isinstance(custom_permissions, list):
                perms_str = ', '.join([str(p).strip() for p in custom_permissions if str(p).strip()])
            elif custom_permissions:
                perms_str = str(custom_permissions).strip()
            else:
                perms_str = ''

            users = cls._read_records('Users', use_cache=False)
            max_id = 0
            for u in users:
                uid_str = u.get('User ID') or u.get('user_id') or ''
                if str(uid_str).startswith('USR-'):
                    try:
                        num = int(str(uid_str).replace('USR-', ''))
                        if num > max_id:
                            max_id = num
                    except ValueError:
                        pass
            user_id = f"USR-{max(max_id + 1, len(users) + 1):04d}"
            pwd_hash = generate_password_hash(password)
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            ws = cls._get_worksheet('Users')
            headers = execute_with_retry(lambda: ws.row_values(1))
            if not headers:
                headers = SHEET_SCHEMAS['Users']

            field_map = {
                'User ID': user_id,
                'Full Name': full_name.strip(),
                'Email': clean_email,
                'Password Hash': pwd_hash,
                'Role': canon_role,
                'Department': dept_str,
                'Is Active': 'TRUE',
                'Created At': now_str,
                'Last Login': '',
                'Custom Permissions': perms_str
            }
            row = [field_map.get(h, '') for h in headers]
            execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Users')

            return {
                'user_id': user_id,
                'User ID': user_id,
                'full_name': full_name.strip(),
                'Full Name': full_name.strip(),
                'email': clean_email,
                'Email': clean_email,
                'role': canon_role,
                'Role': canon_role,
                'department': dept_str,
                'Department': dept_str,
                'is_active': 'TRUE',
                'Is Active': 'TRUE',
                'created_at': now_str,
                'Created At': now_str,
                'custom_permissions': perms_str,
                'Custom Permissions': perms_str
            }

    @classmethod
    def update_user_last_login(cls, email):
        """Updates the Last Login timestamp for a user."""
        clean_email = email.lower().strip()
        ws = cls._get_worksheet('Users')
        rows = execute_with_retry(lambda: ws.get_all_values())
        if not rows or len(rows) < 2:
            return

        headers = [h.strip() for h in rows[0]]
        email_idx = headers.index('Email') + 1 if 'Email' in headers else 3
        login_idx = headers.index('Last Login') + 1 if 'Last Login' in headers else 9
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        for r_idx, r in enumerate(rows[1:], start=2):
            if len(r) >= email_idx and r[email_idx - 1].lower().strip() == clean_email:
                execute_with_retry(lambda: ws.update_cell(r_idx, login_idx, sanitize_cell_value(now_str)))
                cls._invalidate_cache('Users')
                break

    @classmethod
    def count_active_super_admins(cls):
        """Returns the number of active Super Admin accounts."""
        users = cls.get_all_users()
        return sum(1 for u in users if u.get('Role') == 'Super Admin' and str(u.get('Is Active', '')).upper() == 'TRUE')

    @classmethod
    def update_user(cls, user_id, full_name=None, email=None, role=None, department=None, is_active=None, new_password=None, custom_permissions=None):
        """
        Updates an existing user's details in the Google Sheets Users worksheet.
        Preserves User ID, Created At, and password hash (unless new_password provided).
        Validates email uniqueness across other accounts.
        If email changes, safely cascades new email to Complaints to preserve ownership.
        """
        with cls._db_lock:
            ws = cls._get_worksheet('Users')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"User '{user_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('User ID') if 'User ID' in headers else 0
            name_idx = headers.index('Full Name') if 'Full Name' in headers else 1
            email_idx = headers.index('Email') if 'Email' in headers else 2
            pwd_idx = headers.index('Password Hash') if 'Password Hash' in headers else 3
            role_idx = headers.index('Role') if 'Role' in headers else 4
            dept_idx = headers.index('Department') if 'Department' in headers else 5
            active_idx = headers.index('Is Active') if 'Is Active' in headers else 6

            clean_email = email.lower().strip() if email else None
            found_row = None
            old_email = None

            for r_idx, r in enumerate(rows[1:], start=2):
                curr_id = r[id_idx].strip() if len(r) > id_idx else ''
                curr_email = r[email_idx].strip().lower() if len(r) > email_idx else ''
                if curr_id == str(user_id).strip():
                    found_row = r_idx
                    old_email = curr_email
                elif clean_email and curr_email == clean_email:
                    raise ValueError(f"Email '{clean_email}' is already in use by another user ({curr_id}).")

            if not found_row:
                raise ValueError(f"User '{user_id}' not found.")

            current_row = list(rows[found_row - 1])
            while len(current_row) < len(headers):
                current_row.append('')

            if full_name is not None and full_name.strip():
                current_row[name_idx] = sanitize_cell_value(full_name.strip())

            if clean_email:
                current_row[email_idx] = sanitize_cell_value(clean_email)

            if role is not None and role.strip():
                all_roles = [r.get('Name') for r in cls.get_all_roles() if r.get('Name')]
                canon_role = RBACService.canonical_role(role.strip())
                if canon_role not in all_roles and role.strip() not in all_roles:
                    raise ValueError(f"Invalid role '{role}'. Allowed roles: {', '.join(sorted(set(all_roles)))}")
                current_row[role_idx] = sanitize_cell_value(canon_role)

            if department is not None:
                if isinstance(department, list):
                    dept_str = ', '.join(d.strip() for d in department if d.strip())
                else:
                    dept_str = str(department).strip()
                current_row[dept_idx] = sanitize_cell_value(dept_str or 'General')

            if custom_permissions is not None:
                perm_idx = headers.index('Custom Permissions') if 'Custom Permissions' in headers else -1
                if perm_idx >= 0:
                    if isinstance(custom_permissions, list):
                        val = ', '.join([str(p).strip() for p in custom_permissions if str(p).strip()])
                    elif custom_permissions:
                        val = str(custom_permissions).strip()
                    else:
                        val = ''
                    current_row[perm_idx] = sanitize_cell_value(val)

            if is_active is not None:
                val_upper = str(is_active).strip().upper()
                if val_upper in ('ARCHIVED', 'ARCHIVE'):
                    current_row[active_idx] = 'ARCHIVED'
                elif val_upper in ('TRUE', '1', 'YES'):
                    current_row[active_idx] = 'TRUE'
                else:
                    current_row[active_idx] = 'FALSE'

            if new_password and str(new_password).strip():
                if len(str(new_password).strip()) < 6:
                    raise ValueError("Password must be at least 6 characters.")
                current_row[pwd_idx] = generate_password_hash(str(new_password).strip())

            execute_with_retry(lambda: ws.update(f'A{found_row}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Users')

            # Cascade email update to Complaints to preserve complaint ownership if email changed
            if clean_email and old_email and clean_email != old_email:
                cls._cascade_user_email_change(old_email, clean_email)

            cust_perms = current_row[headers.index('Custom Permissions')] if 'Custom Permissions' in headers and len(current_row) > headers.index('Custom Permissions') else ''
            return {
                'User ID': user_id,
                'Full Name': current_row[name_idx],
                'Email': current_row[email_idx],
                'Role': current_row[role_idx],
                'Department': current_row[dept_idx],
                'Is Active': current_row[active_idx],
                'Created At': current_row[headers.index('Created At')] if 'Created At' in headers else '',
                'Custom Permissions': cust_perms
            }

    @classmethod
    def _cascade_user_email_change(cls, old_email, new_email):
        """
        When a user's email is updated, cascades the new email to existing complaints
        where Complainant Email == old_email, preserving complaint ownership.
        """
        try:
            ws = cls._get_worksheet('Complaints')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                return
            headers = [h.strip() for h in rows[0]]
            if 'Complainant Email' not in headers:
                return
            c_email_idx = headers.index('Complainant Email')
            updated = False
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) > c_email_idx and r[c_email_idx].strip().lower() == old_email.lower():
                    r_val = sanitize_cell_value(new_email.lower())
                    col_letter = gspread.utils.rowcol_to_a1(r_idx, c_email_idx + 1)
                    execute_with_retry(lambda cell=col_letter, val=r_val: ws.update(cell, [[val]], value_input_option='USER_ENTERED'))
                    updated = True
            if updated:
                cls._invalidate_cache('Complaints')
        except Exception as e:
            logger.warning(f"Could not cascade email change in complaints: {e}")

    @classmethod
    def get_user_usage(cls, user_id_or_email):
        """
        Analyzes dependencies for a user before deletion or archival:
        - Complaints lodged by user
        - Complaints handled/assigned to user
        - Comments and history entries authored by user
        """
        user = cls.find_user_by_id(user_id_or_email) or cls.find_user_by_email(user_id_or_email)
        if not user:
            return None

        user_email = (user.get('Email') or '').lower().strip()
        user_name = (user.get('Full Name') or '').strip().lower()

        complaints = cls._read_records('Complaints', use_cache=True)
        lodged_count = 0
        handled_count = 0

        for c in complaints:
            comp_email = (c.get('Complainant Email') or '').lower().strip()
            comp_name = (c.get('Employee Name') or c.get('Complainant Name') or '').strip().lower()
            handled_by = (c.get('Handled By') or '').lower().strip()

            if user_email and comp_email == user_email:
                lodged_count += 1
            elif user_name and comp_name == user_name:
                lodged_count += 1

            if user_email and user_email in handled_by:
                handled_count += 1
            elif user_name and user_name in handled_by:
                handled_count += 1

        comments = cls._read_records('Assignments and Comments', use_cache=True)
        comment_count = 0
        for com in comments:
            author_email = (com.get('Author Email') or '').lower().strip()
            author_name = (com.get('Author Name') or '').strip().lower()
            if (user_email and author_email == user_email) or (user_name and author_name == user_name):
                comment_count += 1

        history = cls._read_records('Status History', use_cache=True)
        history_count = 0
        for h in history:
            changed_email = (h.get('Changed By Email') or '').lower().strip()
            changed_name = (h.get('Changed By Name') or '').strip().lower()
            if (user_email and changed_email == user_email) or (user_name and changed_name == user_name):
                history_count += 1

        return {
            'user': user,
            'lodged_complaints_count': lodged_count,
            'handled_complaints_count': handled_count,
            'comments_count': comment_count,
            'history_count': history_count,
            'total_activity_count': lodged_count + handled_count + comment_count + history_count
        }

    @classmethod
    def delete_user(cls, user_id):
        """
        Permanently deletes a user record from the Google Sheets Users worksheet.
        NEVER touches, deletes, or cascade-deletes Complaints, Comments, or History.
        """
        with cls._db_lock:
            ws = cls._get_worksheet('Users')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"User '{user_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('User ID') if 'User ID' in headers else 0

            found_row = None
            for r_idx, r in enumerate(rows[1:], start=2):
                curr_id = r[id_idx].strip() if len(r) > id_idx else ''
                if curr_id == str(user_id).strip():
                    found_row = r_idx
                    break

            if not found_row:
                raise ValueError(f"User '{user_id}' not found.")

            execute_with_retry(lambda: ws.delete_rows(found_row))
            cls._invalidate_cache('Users')
            return True

    # =========================================================================
    # DEPARTMENT OPERATIONS
    # =========================================================================

    @classmethod
    def get_all_departments(cls, include_inactive=False):
        """Returns list of departments. By default, returns active departments."""
        depts = cls._read_records('Departments', use_cache=True)
        if include_inactive:
            return depts
        return [d for d in depts if str(d.get('Is Active', 'TRUE')).upper() == 'TRUE']

    @classmethod
    def get_department_by_id(cls, dept_id):
        """Finds department by Department ID."""
        if not dept_id:
            return None
        target = dept_id.strip()
        depts = cls._read_records('Departments', use_cache=False)
        for d in depts:
            if d.get('Department ID', '').strip() == target:
                return dict(d)
        return None

    @classmethod
    def get_department_by_name(cls, name):
        """Finds department by Name (case-insensitive)."""
        if not name:
            return None
        target = name.strip().lower()
        depts = cls._read_records('Departments', use_cache=True)
        for d in depts:
            if d.get('Name', '').strip().lower() == target:
                return dict(d)
        return None

    @classmethod
    def create_department(cls, name, description='', lead_email=''):
        """Creates a new department in the Departments sheet."""
        with cls._db_lock:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Department name is required.")

            depts = cls._read_records('Departments', use_cache=False)
            for d in depts:
                if d.get('Name', '').strip().lower() == clean_name.lower():
                    raise ValueError(f"Department '{clean_name}' already exists.")

            dept_id = f"DEP-{len(depts) + 1:03d}"
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            row = [
                dept_id,
                clean_name,
                description.strip(),
                lead_email.strip().lower(),
                'TRUE',
                now_str
            ]

            ws = cls._get_worksheet('Departments')
            execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Departments')

            return {
                'Department ID': dept_id,
                'Name': clean_name,
                'Description': description.strip(),
                'Lead Email': lead_email.strip().lower(),
                'Is Active': 'TRUE',
                'Created At': now_str
            }

    @classmethod
    def update_department(cls, dept_id, name, description='', lead_email='', is_active='TRUE'):
        """
        Updates an existing department's details in Google Sheets.
        If the department name changed, safely updates all complaints linked to the old name.
        """
        with cls._db_lock:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Department name is required.")

            ws = cls._get_worksheet('Departments')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Department '{dept_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Department ID') + 1 if 'Department ID' in headers else 1
            name_idx = headers.index('Name') + 1 if 'Name' in headers else 2
            desc_idx = headers.index('Description') + 1 if 'Description' in headers else 3
            lead_idx = headers.index('Lead Email') + 1 if 'Lead Email' in headers else 4
            active_idx = headers.index('Is Active') + 1 if 'Is Active' in headers else 5

            found_row = None
            old_name = None
            for r_idx, r in enumerate(rows[1:], start=2):
                curr_id = r[id_idx - 1].strip() if len(r) >= id_idx else ''
                curr_name = r[name_idx - 1].strip() if len(r) >= name_idx else ''
                if curr_id == dept_id.strip():
                    found_row = r_idx
                    old_name = curr_name
                elif curr_name.lower() == clean_name.lower():
                    raise ValueError(f"Another department named '{clean_name}' already exists.")

            if not found_row:
                raise ValueError(f"Department '{dept_id}' not found.")

            current_row = list(rows[found_row - 1])
            while len(current_row) < len(headers):
                current_row.append('')

            current_row[name_idx - 1] = sanitize_cell_value(clean_name)
            current_row[desc_idx - 1] = sanitize_cell_value(description.strip())
            current_row[lead_idx - 1] = sanitize_cell_value(lead_email.strip().lower())
            current_row[active_idx - 1] = 'TRUE' if str(is_active).upper() in ('TRUE', '1', 'YES') else 'FALSE'

            execute_with_retry(lambda: ws.update(f'A{found_row}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Departments')

            # If department name changed, update linked complaints in Complaints worksheet
            if old_name and old_name != clean_name:
                cls._rename_department_in_complaints(old_name, clean_name)

            return {
                'Department ID': dept_id,
                'Name': clean_name,
                'Description': description.strip(),
                'Lead Email': lead_email.strip().lower(),
                'Is Active': current_row[active_idx - 1],
                'Created At': current_row[headers.index('Created At')] if 'Created At' in headers else ''
            }

    @classmethod
    def get_department_usage(cls, dept_id_or_name):
        """
        Counts complaints linked to a department by ID or Name.
        Returns a tuple: (department_dict, count_linked, list_of_complaint_ids).
        """
        dept = None
        depts = cls._read_records('Departments', use_cache=True)
        for d in depts:
            if d.get('Department ID', '').strip() == dept_id_or_name.strip() or \
               d.get('Name', '').strip().lower() == dept_id_or_name.strip().lower():
                dept = d
                break

        if not dept:
            raise ValueError(f"Department '{dept_id_or_name}' not found.")

        target_name = dept['Name'].strip().lower()
        complaints = cls._read_records('Complaints', use_cache=False)
        linked_ids = []

        for c in complaints:
            raw_dept = (c.get('Department Name') or c.get('Department') or '').strip()
            # Support both single department and comma-separated multi-department strings
            dept_parts = [p.strip().lower() for p in raw_dept.split(',') if p.strip()]
            if target_name in dept_parts:
                linked_ids.append(c.get('Complaint ID'))

        return dept, len(linked_ids), linked_ids

    @classmethod
    def delete_department(cls, dept_id, reassign_to_dept_id_or_name=None):
        """
        Deletes a department from the Departments worksheet.
        Enforces safety: prevents deletion of departments linked to complaints
        unless those complaints are safely reassigned.
        """
        with cls._db_lock:
            dept, usage_count, linked_ids = cls.get_department_usage(dept_id)

            if usage_count > 0:
                if not reassign_to_dept_id_or_name:
                    raise ValueError(
                        f"Cannot delete department '{dept['Name']}': It is currently linked to {usage_count} "
                        f"complaint(s) ({', '.join(linked_ids[:5])}{'...' if len(linked_ids) > 5 else ''}). "
                        f"Please reassign these complaints before deleting this department."
                    )
                
                # Verify replacement department
                new_dept = None
                depts = cls._read_records('Departments', use_cache=False)
                for d in depts:
                    if d.get('Department ID', '').strip() == reassign_to_dept_id_or_name.strip() or \
                       d.get('Name', '').strip().lower() == reassign_to_dept_id_or_name.strip().lower():
                        new_dept = d
                        break

                if not new_dept:
                    raise ValueError(f"Replacement department '{reassign_to_dept_id_or_name}' not found.")
                if new_dept['Department ID'] == dept['Department ID']:
                    raise ValueError("Cannot reassign complaints to the department being deleted.")

                # Reassign linked complaints
                cls._reassign_department_in_complaints(dept['Name'], new_dept['Name'])

            # Find row in Departments sheet and delete
            ws = cls._get_worksheet('Departments')
            rows = execute_with_retry(lambda: ws.get_all_values())
            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Department ID') + 1 if 'Department ID' in headers else 1

            found_row = None
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) >= id_idx and r[id_idx - 1].strip() == dept_id.strip():
                    found_row = r_idx
                    break

            if not found_row:
                raise ValueError(f"Department '{dept_id}' not found in worksheet.")

            execute_with_retry(lambda: ws.delete_rows(found_row))
            cls._invalidate_cache('Departments')
            return True

    @classmethod
    def _reassign_department_in_complaints(cls, old_name, new_name):
        """
        Reassigns old_name to new_name across all complaints in the Complaints worksheet.
        Handles multi-department comma-separated values correctly.
        """
        ws = cls._get_worksheet('Complaints')
        rows = execute_with_retry(lambda: ws.get_all_values())
        if not rows or len(rows) < 2:
            return

        headers = [h.strip() for h in rows[0]]
        dept_idx = headers.index('Department Name') + 1 if 'Department Name' in headers else (headers.index('Department') + 1 if 'Department' in headers else -1)
        if dept_idx < 1:
            return

        target_old = old_name.strip().lower()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        updated_idx = headers.index('Last Updated At') + 1 if 'Last Updated At' in headers else -1

        for r_idx, r in enumerate(rows[1:], start=2):
            raw_val = r[dept_idx - 1].strip() if len(r) >= dept_idx else ''
            parts = [p.strip() for p in raw_val.split(',') if p.strip()]
            parts_lower = [p.lower() for p in parts]

            if target_old in parts_lower:
                new_parts = []
                for p in parts:
                    if p.lower() == target_old:
                        if new_name not in new_parts:
                            new_parts.append(new_name)
                    else:
                        if p not in new_parts:
                            new_parts.append(p)
                new_str = ", ".join(new_parts)

                current_row = list(r)
                while len(current_row) < len(headers):
                    current_row.append('')
                current_row[dept_idx - 1] = sanitize_cell_value(new_str)
                if updated_idx > 0:
                    current_row[updated_idx - 1] = sanitize_cell_value(now_str)

                execute_with_retry(lambda: ws.update(f'A{r_idx}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))

        cls._invalidate_cache('Complaints')

    @classmethod
    def _rename_department_in_complaints(cls, old_name, new_name):
        """Aliases to _reassign_department_in_complaints."""
        cls._reassign_department_in_complaints(old_name, new_name)

    # =========================================================================
    # ROLE MANAGEMENT OPERATIONS (RBAC)
    # =========================================================================

    @classmethod
    def get_all_roles(cls):
        """Returns all role configurations (system and custom roles)."""
        roles = cls._read_records('Roles', use_cache=True)
        if not roles:
            return [dict(tpl) for tpl in DEFAULT_ROLE_TEMPLATES.values()]
        return roles

    @classmethod
    def get_role_by_id(cls, role_id):
        if not role_id: return None
        for r in cls.get_all_roles():
            if r.get('Role ID', '').strip() == str(role_id).strip():
                return dict(r)
        return None

    @classmethod
    def get_role_by_name(cls, name):
        if not name: return None
        canon = RBACService.canonical_role(name)
        for r in cls.get_all_roles():
            if r.get('Name', '').strip().lower() == canon.lower():
                return dict(r)
        return None

    @classmethod
    def create_role(cls, name, description='', data_scope='DEPARTMENT', permissions=None, is_system_role=False):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Role name is required.")
        if cls.get_role_by_name(clean_name):
            raise ValueError(f"Role '{clean_name}' already exists.")

        scope_clean = str(data_scope).strip().upper()
        if scope_clean not in ('ALL', 'DEPARTMENT', 'OWN'):
            scope_clean = 'DEPARTMENT'

        perms_list = permissions if permissions is not None else []
        if isinstance(perms_list, str):
            perms_str = perms_list.strip()
        else:
            perms_str = ','.join(p.strip() for p in perms_list if p.strip())

        with cls._db_lock:
            existing = cls.get_all_roles()
            role_id = f"ROLE-{len(existing) + 1:03d}"
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            row = [
                role_id,
                clean_name,
                description.strip(),
                scope_clean,
                perms_str,
                'TRUE' if is_system_role else 'FALSE',
                now_str,
                now_str
            ]
            ws = cls._get_worksheet('Roles')
            execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Roles')

            return {
                'Role ID': role_id,
                'Name': clean_name,
                'Description': description.strip(),
                'Data Scope': scope_clean,
                'Permissions': perms_str,
                'Is System Role': 'TRUE' if is_system_role else 'FALSE',
                'Created At': now_str,
                'Last Updated At': now_str
            }

    @classmethod
    def update_role(cls, role_id, name=None, description=None, data_scope=None, permissions=None):
        with cls._db_lock:
            ws = cls._get_worksheet('Roles')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Role '{role_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Role ID') if 'Role ID' in headers else 0
            name_idx = headers.index('Name') if 'Name' in headers else 1
            desc_idx = headers.index('Description') if 'Description' in headers else 2
            scope_idx = headers.index('Data Scope') if 'Data Scope' in headers else 3
            perms_idx = headers.index('Permissions') if 'Permissions' in headers else 4
            system_idx = headers.index('Is System Role') if 'Is System Role' in headers else 5
            updated_idx = headers.index('Last Updated At') if 'Last Updated At' in headers else 7

            target_row_idx = None
            old_name = None
            is_system = False

            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) > id_idx and r[id_idx].strip() == str(role_id).strip():
                    target_row_idx = r_idx
                    old_name = r[name_idx].strip() if len(r) > name_idx else ''
                    is_system = (r[system_idx].strip().upper() == 'TRUE') if len(r) > system_idx else False
                    break

            if not target_row_idx:
                raise ValueError(f"Role '{role_id}' not found.")

            current_row = list(rows[target_row_idx - 1])
            while len(current_row) < len(headers):
                current_row.append('')

            if name is not None and name.strip():
                clean_name = name.strip()
                if is_system and clean_name.lower() != old_name.lower():
                    raise ValueError(f"System role '{old_name}' cannot be renamed.")
                for r in rows[1:]:
                    if r[id_idx].strip() != str(role_id).strip() and r[name_idx].strip().lower() == clean_name.lower():
                        raise ValueError(f"Another role named '{clean_name}' already exists.")
                current_row[name_idx] = sanitize_cell_value(clean_name)
                if old_name and clean_name != old_name:
                    cls._rename_role_in_users(old_name, clean_name)

            if description is not None:
                current_row[desc_idx] = sanitize_cell_value(description.strip())

            if data_scope is not None:
                scope_upper = str(data_scope).strip().upper()
                if scope_upper in ('ALL', 'DEPARTMENT', 'OWN'):
                    current_row[scope_idx] = scope_upper

            if permissions is not None:
                if old_name == 'Super Admin':
                    current_row[perms_idx] = '*'
                else:
                    if isinstance(permissions, str):
                        current_row[perms_idx] = permissions.strip()
                    else:
                        current_row[perms_idx] = ','.join(p.strip() for p in permissions if p.strip())

            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            current_row[updated_idx] = now_str

            for c_idx, val in enumerate(current_row, start=1):
                execute_with_retry(lambda ci=c_idx, v=val: ws.update_cell(target_row_idx, ci, sanitize_cell_value(v)))

            cls._invalidate_cache('Roles')
            return dict(zip(headers, current_row))

    @classmethod
    def get_role_usage(cls, role_id_or_name):
        role = cls.get_role_by_id(role_id_or_name) or cls.get_role_by_name(role_id_or_name)
        if not role:
            raise ValueError(f"Role '{role_id_or_name}' not found.")
        target_name = (role.get('Name') or role.get('role_name') or '').strip().lower()
        users = cls.get_all_users()
        linked_users = [u for u in users if (u.get('Role') or u.get('role') or '').strip().lower() == target_name]
        return role, len(linked_users), linked_users

    @classmethod
    def delete_role(cls, role_id):
        role = cls.get_role_by_id(role_id)
        if not role:
            raise ValueError(f"Role '{role_id}' not found.")
        if str(role.get('Is System Role', '')).upper() == 'TRUE' or str(role.get('is_system', '')).upper() == 'TRUE':
            raise ValueError(f"Cannot delete system role '{role.get('Name') or role.get('role_name')}'. System roles are permanently protected.")

        role, usage_count, linked_users = cls.get_role_usage(role_id)
        if usage_count > 0:
            linked_emails = [u.get('Email', '') if isinstance(u, dict) else str(u) for u in linked_users]
            raise ValueError(
                f"Cannot delete role '{role.get('Name') or role.get('role_name')}': Assigned to {usage_count} user(s) "
                f"({', '.join(linked_emails[:3])}{'...' if len(linked_emails) > 3 else ''}). "
                f"Please reassign these users to another role before deleting."
            )

        with cls._db_lock:
            ws = cls._get_worksheet('Roles')
            rows = execute_with_retry(lambda: ws.get_all_values())
            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Role ID') if 'Role ID' in headers else 0

            target_row_idx = None
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) > id_idx and r[id_idx].strip() == str(role_id).strip():
                    target_row_idx = r_idx
                    break

            if not target_row_idx:
                raise ValueError(f"Role '{role_id}' not found.")

            execute_with_retry(lambda: ws.delete_rows(target_row_idx))
            cls._invalidate_cache('Roles')
            return True

    @classmethod
    def _rename_role_in_users(cls, old_role, new_role):
        users_ws = cls._get_worksheet('Users')
        rows = execute_with_retry(lambda: users_ws.get_all_values())
        if not rows or len(rows) < 2: return
        headers = [h.strip() for h in rows[0]]
        role_idx = headers.index('Role') + 1 if 'Role' in headers else 5
        for r_idx, r in enumerate(rows[1:], start=2):
            if len(r) >= role_idx and r[role_idx - 1].strip().lower() == old_role.strip().lower():
                execute_with_retry(lambda ri=r_idx, ri_col=role_idx: users_ws.update_cell(ri, ri_col, sanitize_cell_value(new_role)))
        cls._invalidate_cache('Users')

    # =========================================================================
    # AUDIT LOGGING OPERATIONS
    # =========================================================================

    @classmethod
    def log_audit_event(
        cls,
        user_id='',
        user_email='',
        user_role='',
        action='',
        module='',
        record_id='',
        department='',
        details='',
        previous_state='',
        new_state='',
        result='SUCCESS',
        ip_address=''
    ):
        """Appends an immutable audit log entry to the Audit_Logs worksheet."""
        try:
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            log_id = f"LOG-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

            row = [
                log_id,
                user_id or '',
                user_email or '',
                user_role or '',
                action or '',
                module or '',
                str(record_id or ''),
                department or '',
                str(details or ''),
                str(previous_state or ''),
                str(new_state or ''),
                result or 'SUCCESS',
                ip_address or '',
                now_str
            ]
            ws = cls._get_worksheet('Audit_Logs')
            execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Audit_Logs')
            return log_id
        except Exception as e:
            logger.error(f"Failed to write audit log event: {e}")
            return None

    @classmethod
    def get_audit_logs(cls, limit=100, module=None, action=None, user_email=None):
        """Reads security audit log records, ordered newest first."""
        records = cls._read_records('Audit_Logs', use_cache=False)
        filtered = []
        for r in reversed(records):
            if module and (r.get('Module') or '').lower() != module.lower():
                continue
            if action and (r.get('Action') or '').lower() != action.lower():
                continue
            if user_email and (r.get('User Email') or '').lower() != user_email.lower():
                continue
            filtered.append(r)
            if len(filtered) >= limit:
                break
        return filtered

    # =========================================================================
    # COMPLAINT TYPES MASTER OPERATIONS
    # =========================================================================

    @classmethod
    def get_all_complaint_types(cls, include_inactive=False):
        """Returns list of complaint types. By default, returns active only."""
        types = cls._read_records('Complaint_Types', use_cache=True)
        if include_inactive:
            return types
        return [t for t in types if str(t.get('Is Active', 'TRUE')).upper() == 'TRUE']

    @classmethod
    def get_complaint_type_by_id(cls, type_id):
        """Finds complaint type by Type ID."""
        if not type_id:
            return None
        target = type_id.strip()
        types = cls._read_records('Complaint_Types', use_cache=False)
        for t in types:
            if t.get('Type ID', '').strip() == target:
                return dict(t)
        return None

    @classmethod
    def get_complaint_type_by_name(cls, name):
        """Finds complaint type by Name (case-insensitive)."""
        if not name:
            return None
        target = name.strip().lower()
        types = cls._read_records('Complaint_Types', use_cache=True)
        for t in types:
            if t.get('Name', '').strip().lower() == target:
                return dict(t)
        return None

    @classmethod
    def create_complaint_type(cls, name, description=''):
        """Creates a new complaint type in the Complaint_Types sheet."""
        with cls._db_lock:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Complaint Type name is required.")

            types = cls._read_records('Complaint_Types', use_cache=False)
            for t in types:
                if t.get('Name', '').strip().lower() == clean_name.lower():
                    raise ValueError(f"Complaint Type '{clean_name}' already exists.")

            type_id = f"CT-{len(types) + 1:03d}"
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            row = [
                type_id,
                clean_name,
                description.strip(),
                'TRUE',
                now_str
            ]

            ws = cls._get_worksheet('Complaint_Types')
            execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Complaint_Types')

            return {
                'Type ID': type_id,
                'Name': clean_name,
                'Description': description.strip(),
                'Is Active': 'TRUE',
                'Created At': now_str
            }

    @classmethod
    def update_complaint_type(cls, type_id, name, description='', is_active='TRUE'):
        """Updates an existing complaint type in Google Sheets."""
        with cls._db_lock:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Complaint Type name is required.")

            ws = cls._get_worksheet('Complaint_Types')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Complaint Type '{type_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Type ID') + 1 if 'Type ID' in headers else 1
            name_idx = headers.index('Name') + 1 if 'Name' in headers else 2
            desc_idx = headers.index('Description') + 1 if 'Description' in headers else 3
            active_idx = headers.index('Is Active') + 1 if 'Is Active' in headers else 4

            found_row = None
            old_name = None
            for r_idx, r in enumerate(rows[1:], start=2):
                curr_id = r[id_idx - 1].strip() if len(r) >= id_idx else ''
                curr_name = r[name_idx - 1].strip() if len(r) >= name_idx else ''
                if curr_id == type_id.strip():
                    found_row = r_idx
                    old_name = curr_name
                elif curr_name.lower() == clean_name.lower():
                    raise ValueError(f"Another Complaint Type named '{clean_name}' already exists.")

            if not found_row:
                raise ValueError(f"Complaint Type '{type_id}' not found.")

            current_row = list(rows[found_row - 1])
            while len(current_row) < len(headers):
                current_row.append('')

            current_row[name_idx - 1] = sanitize_cell_value(clean_name)
            current_row[desc_idx - 1] = sanitize_cell_value(description.strip())
            current_row[active_idx - 1] = 'TRUE' if str(is_active).upper() in ('TRUE', '1', 'YES') else 'FALSE'

            execute_with_retry(lambda: ws.update(f'A{found_row}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Complaint_Types')

            # If name changed, rename across linked complaints
            if old_name and old_name != clean_name:
                cls._rename_complaint_type_in_complaints(old_name, clean_name)

            return {
                'Type ID': type_id,
                'Name': clean_name,
                'Description': description.strip(),
                'Is Active': current_row[active_idx - 1],
                'Created At': current_row[headers.index('Created At')] if 'Created At' in headers else ''
            }

    @classmethod
    def get_complaint_type_usage(cls, type_id_or_name):
        """
        Counts complaints linked to a complaint type by ID or Name.
        Returns: (type_dict, count_linked, list_of_complaint_ids).
        """
        ct = None
        types = cls._read_records('Complaint_Types', use_cache=True)
        for t in types:
            if t.get('Type ID', '').strip() == type_id_or_name.strip() or \
               t.get('Name', '').strip().lower() == type_id_or_name.strip().lower():
                ct = t
                break

        if not ct:
            raise ValueError(f"Complaint Type '{type_id_or_name}' not found.")

        target_name = ct['Name'].strip().lower()
        complaints = cls._read_records('Complaints', use_cache=False)
        linked_ids = []

        for c in complaints:
            raw_type = (c.get('Complain Types') or c.get('Subject') or '').strip().lower()
            if target_name == raw_type:
                linked_ids.append(c.get('Complaint ID'))

        return ct, len(linked_ids), linked_ids

    @classmethod
    def delete_complaint_type(cls, type_id):
        """
        Deletes a complaint type from Complaint_Types sheet.
        Blocks deletion if linked to any complaints to prevent data inconsistency.
        """
        with cls._db_lock:
            ct, usage_count, linked_ids = cls.get_complaint_type_usage(type_id)

            if usage_count > 0:
                raise ValueError(
                    f"Cannot delete Complaint Type '{ct['Name']}': It is currently used in {usage_count} "
                    f"complaint(s) ({', '.join(linked_ids[:5])}{'...' if len(linked_ids) > 5 else ''}). "
                    f"Please deactivate this complaint type instead to preserve historical records."
                )

            ws = cls._get_worksheet('Complaint_Types')
            rows = execute_with_retry(lambda: ws.get_all_values())
            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Type ID') + 1 if 'Type ID' in headers else 1

            found_row = None
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) >= id_idx and r[id_idx - 1].strip() == type_id.strip():
                    found_row = r_idx
                    break

            if not found_row:
                raise ValueError(f"Complaint Type '{type_id}' not found in worksheet.")

            execute_with_retry(lambda: ws.delete_rows(found_row))
            cls._invalidate_cache('Complaint_Types')
            return True

    @classmethod
    def _rename_complaint_type_in_complaints(cls, old_name, new_name):
        """Renames complaint type across all complaints in Complaints sheet."""
        ws = cls._get_worksheet('Complaints')
        rows = execute_with_retry(lambda: ws.get_all_values())
        if not rows or len(rows) < 2:
            return

        headers = [h.strip() for h in rows[0]]
        type_idx = headers.index('Complain Types') + 1 if 'Complain Types' in headers else (headers.index('Subject') + 1 if 'Subject' in headers else -1)
        if type_idx < 1:
            return

        target_old = old_name.strip().lower()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        updated_idx = headers.index('Last Updated At') + 1 if 'Last Updated At' in headers else -1

        for r_idx, r in enumerate(rows[1:], start=2):
            raw_val = r[type_idx - 1].strip() if len(r) >= type_idx else ''
            if raw_val.lower() == target_old:
                current_row = list(r)
                while len(current_row) < len(headers):
                    current_row.append('')
                current_row[type_idx - 1] = sanitize_cell_value(new_name)
                if updated_idx > 0:
                    current_row[updated_idx - 1] = sanitize_cell_value(now_str)
                execute_with_retry(lambda: ws.update(f'A{r_idx}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))

        cls._invalidate_cache('Complaints')

    # =========================================================================
    # SALESPERSONS MASTER OPERATIONS
    # =========================================================================

    @classmethod
    def get_all_salespersons(cls, include_inactive=False):
        """Returns list of salespersons. By default, returns active only."""
        salespersons = cls._read_records('Salespersons', use_cache=True)
        if include_inactive:
            return salespersons
        return [sp for sp in salespersons if str(sp.get('Is Active', 'TRUE')).upper() == 'TRUE']

    @classmethod
    def get_salesperson_by_id(cls, sp_id):
        """Finds salesperson by Salesperson ID."""
        if not sp_id:
            return None
        target = sp_id.strip()
        salespersons = cls._read_records('Salespersons', use_cache=False)
        for sp in salespersons:
            if sp.get('Salesperson ID', '').strip() == target:
                return dict(sp)
        return None

    @classmethod
    def get_salesperson_by_name(cls, name):
        """Finds salesperson by Name (case-insensitive)."""
        if not name:
            return None
        target = name.strip().lower()
        salespersons = cls._read_records('Salespersons', use_cache=True)
        for sp in salespersons:
            if sp.get('Name', '').strip().lower() == target:
                return dict(sp)
        return None

    @classmethod
    def create_salesperson(cls, name, phone='', email=''):
        """Creates a new salesperson in the Salespersons sheet."""
        with cls._db_lock:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Salesperson name is required.")

            salespersons = cls._read_records('Salespersons', use_cache=False)
            for sp in salespersons:
                if sp.get('Name', '').strip().lower() == clean_name.lower():
                    raise ValueError(f"Salesperson '{clean_name}' already exists.")

            sp_id = f"SP-{len(salespersons) + 1:03d}"
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            row = [
                sp_id,
                clean_name,
                phone.strip(),
                email.strip().lower(),
                'TRUE',
                now_str
            ]

            ws = cls._get_worksheet('Salespersons')
            execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Salespersons')

            return {
                'Salesperson ID': sp_id,
                'Name': clean_name,
                'Phone': phone.strip(),
                'Email': email.strip().lower(),
                'Is Active': 'TRUE',
                'Created At': now_str
            }

    @classmethod
    def update_salesperson(cls, sp_id, name, phone='', email='', is_active='TRUE'):
        """Updates an existing salesperson in Google Sheets."""
        with cls._db_lock:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Salesperson name is required.")

            ws = cls._get_worksheet('Salespersons')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Salesperson '{sp_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Salesperson ID') + 1 if 'Salesperson ID' in headers else 1
            name_idx = headers.index('Name') + 1 if 'Name' in headers else 2
            phone_idx = headers.index('Phone') + 1 if 'Phone' in headers else 3
            email_idx = headers.index('Email') + 1 if 'Email' in headers else 4
            active_idx = headers.index('Is Active') + 1 if 'Is Active' in headers else 5

            found_row = None
            old_name = None
            for r_idx, r in enumerate(rows[1:], start=2):
                curr_id = r[id_idx - 1].strip() if len(r) >= id_idx else ''
                curr_name = r[name_idx - 1].strip() if len(r) >= name_idx else ''
                if curr_id == sp_id.strip():
                    found_row = r_idx
                    old_name = curr_name
                elif curr_name.lower() == clean_name.lower():
                    raise ValueError(f"Another Salesperson named '{clean_name}' already exists.")

            if not found_row:
                raise ValueError(f"Salesperson '{sp_id}' not found.")

            current_row = list(rows[found_row - 1])
            while len(current_row) < len(headers):
                current_row.append('')

            current_row[name_idx - 1] = sanitize_cell_value(clean_name)
            current_row[phone_idx - 1] = sanitize_cell_value(phone.strip())
            current_row[email_idx - 1] = sanitize_cell_value(email.strip().lower())
            current_row[active_idx - 1] = 'TRUE' if str(is_active).upper() in ('TRUE', '1', 'YES') else 'FALSE'

            execute_with_retry(lambda: ws.update(f'A{found_row}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Salespersons')

            # If name changed, rename across linked complaints
            if old_name and old_name != clean_name:
                cls._rename_salesperson_in_complaints(old_name, clean_name)

            return {
                'Salesperson ID': sp_id,
                'Name': clean_name,
                'Phone': phone.strip(),
                'Email': email.strip().lower(),
                'Is Active': current_row[active_idx - 1],
                'Created At': current_row[headers.index('Created At')] if 'Created At' in headers else ''
            }

    @classmethod
    def get_salesperson_usage(cls, sp_id_or_name):
        """
        Counts complaints linked to a salesperson by ID or Name.
        Returns: (sp_dict, count_linked, list_of_complaint_ids).
        """
        sp = None
        salespersons = cls._read_records('Salespersons', use_cache=True)
        for s in salespersons:
            if s.get('Salesperson ID', '').strip() == sp_id_or_name.strip() or \
               s.get('Name', '').strip().lower() == sp_id_or_name.strip().lower():
                sp = s
                break

        if not sp:
            raise ValueError(f"Salesperson '{sp_id_or_name}' not found.")

        target_name = sp['Name'].strip().lower()
        complaints = cls._read_records('Complaints', use_cache=False)
        linked_ids = []

        for c in complaints:
            raw_sp = (c.get('Salesperson Name') or '').strip().lower()
            if target_name == raw_sp:
                linked_ids.append(c.get('Complaint ID'))

        return sp, len(linked_ids), linked_ids

    @classmethod
    def delete_salesperson(cls, sp_id):
        """
        Deletes a salesperson from Salespersons sheet.
        Blocks deletion if linked to any complaints to prevent data inconsistency.
        """
        with cls._db_lock:
            sp, usage_count, linked_ids = cls.get_salesperson_usage(sp_id)

            if usage_count > 0:
                raise ValueError(
                    f"Cannot delete Salesperson '{sp['Name']}': Referenced in {usage_count} "
                    f"complaint(s) ({', '.join(linked_ids[:5])}{'...' if len(linked_ids) > 5 else ''}). "
                    f"Please deactivate this salesperson instead to preserve historical records."
                )

            ws = cls._get_worksheet('Salespersons')
            rows = execute_with_retry(lambda: ws.get_all_values())
            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Salesperson ID') + 1 if 'Salesperson ID' in headers else 1

            found_row = None
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) >= id_idx and r[id_idx - 1].strip() == sp_id.strip():
                    found_row = r_idx
                    break

            if not found_row:
                raise ValueError(f"Salesperson '{sp_id}' not found in worksheet.")

            execute_with_retry(lambda: ws.delete_rows(found_row))
            cls._invalidate_cache('Salespersons')
            return True

    @classmethod
    def _rename_salesperson_in_complaints(cls, old_name, new_name):
        """Renames salesperson across all complaints in Complaints sheet."""
        ws = cls._get_worksheet('Complaints')
        rows = execute_with_retry(lambda: ws.get_all_values())
        if not rows or len(rows) < 2:
            return

        headers = [h.strip() for h in rows[0]]
        sp_idx = headers.index('Salesperson Name') + 1 if 'Salesperson Name' in headers else -1
        if sp_idx < 1:
            return

        target_old = old_name.strip().lower()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        updated_idx = headers.index('Last Updated At') + 1 if 'Last Updated At' in headers else -1

        for r_idx, r in enumerate(rows[1:], start=2):
            raw_val = r[sp_idx - 1].strip() if len(r) >= sp_idx else ''
            if raw_val.lower() == target_old:
                current_row = list(r)
                while len(current_row) < len(headers):
                    current_row.append('')
                current_row[sp_idx - 1] = sanitize_cell_value(new_name)
                if updated_idx > 0:
                    current_row[updated_idx - 1] = sanitize_cell_value(now_str)
                execute_with_retry(lambda: ws.update(f'A{r_idx}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))

        cls._invalidate_cache('Complaints')

    # =========================================================================
    # COMPLAINT OPERATIONS
    # =========================================================================

    @classmethod
    def _normalize_complaint_record(cls, record):
        """
        Normalizes a complaint record ensuring all 21 canonical fields,
        system fields, and backwards compatibility aliases exist.
        """
        if not record:
            return record
        c = dict(record)

        # Ensure all canonical fields are present
        canonical_fields = [
            'Employee Name', 'Complain Date', 'Customer Name', 'Invoice No.',
            'Complain Types', 'Priority', 'Remarks', 'Complain Details',
            'Customer Mobile No.', 'Department Name', 'Category', 'Vendor Name',
            'Salesperson Name', 'Status', 'Closing Date', 'Solution Given',
            'Handled By', 'Total Time Taken', 'Comments', 'GR Amount',
            'GR Expected Date', 'From Department'
        ]
        for f in canonical_fields:
            if f not in c:
                c[f] = ''

        # Bi-directional mapping between legacy keys and canonical keys
        if not c.get('Complain Types') and c.get('Subject'):
            c['Complain Types'] = c['Subject']
        if not c.get('Complain Details') and c.get('Description'):
            c['Complain Details'] = c['Description']
        if not c.get('Department Name') and c.get('Department'):
            c['Department Name'] = c['Department']
        if not c.get('Customer Name') and c.get('Complainant Name'):
            c['Customer Name'] = c['Complainant Name']
        if not c.get('Handled By') and c.get('Assigned To Name'):
            c['Handled By'] = c['Assigned To Name']
        if not c.get('From Department') and c.get('from_department'):
            c['From Department'] = c.get('from_department')

        # Ensure legacy aliases exist for existing views and components
        c['Subject'] = c.get('Complain Types') or ''
        c['Description'] = c.get('Complain Details') or ''
        c['Department'] = c.get('Department Name') or 'General'
        c['Complainant Name'] = c.get('Customer Name') or 'Anonymous'
        c['Assigned To Name'] = c.get('Handled By') or 'Unassigned'
        c['Priority'] = (c.get('Priority') or 'Medium').capitalize()
        c['Status'] = c.get('Status') or 'Submitted'
        c['Resolved At'] = c.get('Closing Date') or c.get('Resolved At', '')
        c['Resolution Summary'] = c.get('Solution Given') or c.get('Resolution Summary', '')
        c['from_department'] = c.get('From Department') or 'None'

        return c

    @classmethod
    def generate_complaint_id(cls):
        """Generates a stable, collision-resistant unique Complaint ID."""
        date_part = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')
        random_suffix = uuid.uuid4().hex[:4].upper()
        return f"CMP-{date_part}-{random_suffix}"

    @classmethod
    def lodge_complaint(cls, **kwargs):
        """
        Creates a new complaint in the Complaints worksheet with all 21 fields
        and records initial Status History. Accepts canonical or legacy keyword args.
        """
        with cls._db_lock:
            complaint_id = cls.generate_complaint_id()
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')
            today_str = now_dt.strftime('%Y-%m-%d')

            # Extract fields with sensible fallbacks and legacy compatibility
            complainant_email = (
                kwargs.get('complainant_email') or kwargs.get('email') or ''
            ).strip().lower()

            customer_name = (
                kwargs.get('customer_name') or kwargs.get('complainant_name') or
                kwargs.get('employee_name') or 'Anonymous'
            ).strip()

            employee_name = (
                kwargs.get('employee_name') or kwargs.get('customer_name') or customer_name
            ).strip()

            complain_date = (kwargs.get('complain_date') or today_str).strip()
            invoice_no = (kwargs.get('invoice_no') or '').strip()

            complain_types = (
                kwargs.get('complain_types') or kwargs.get('subject') or 'General'
            ).strip()

            priority = (kwargs.get('priority') or 'Medium').capitalize()
            remarks = (kwargs.get('remarks') or '').strip()

            complain_details = (
                kwargs.get('complain_details') or kwargs.get('description') or remarks or 'No details provided'
            ).strip()

            customer_mobile_no = (kwargs.get('customer_mobile_no') or '').strip()

            department_name = (
                kwargs.get('department_name') or kwargs.get('department') or 'General'
            ).strip()

            category = (kwargs.get('category') or '').strip()
            vendor_name = (kwargs.get('vendor_name') or '').strip()
            salesperson_name = (kwargs.get('salesperson_name') or '').strip()
            status = (kwargs.get('status') or 'Submitted').strip()
            closing_date = (kwargs.get('closing_date') or '').strip()
            solution_given = (kwargs.get('solution_given') or '').strip()
            handled_by = (
                kwargs.get('handled_by') or kwargs.get('assigned_to_name') or 'Unassigned'
            ).strip()

            total_time_taken = (kwargs.get('total_time_taken') or '').strip()
            if not total_time_taken and closing_date and complain_date:
                try:
                    d1 = datetime.datetime.strptime(complain_date, '%Y-%m-%d')
                    d2 = datetime.datetime.strptime(closing_date, '%Y-%m-%d')
                    diff_days = (d2 - d1).days
                    total_time_taken = f"{max(0, diff_days)} day{'s' if diff_days != 1 else ''}"
                except Exception:
                    pass

            comments = (kwargs.get('comments') or '').strip()
            gr_amount = str(kwargs.get('gr_amount') or '').strip()
            gr_expected_date = (kwargs.get('gr_expected_date') or '').strip()
            attachment_link = (kwargs.get('attachment_link') or kwargs.get('attachment_url') or '').strip()
            from_department = (kwargs.get('from_department') or kwargs.get('From Department') or 'None').strip()
            if from_department not in ('Online', 'Offline', 'Packing', 'None'):
                from_department = 'None'

            # 1. Write Complaint record to Complaints worksheet
            complaints_ws = cls._get_worksheet('Complaints')
            headers = execute_with_retry(lambda: complaints_ws.row_values(1))
            if not headers:
                headers = SHEET_SCHEMAS['Complaints']
                complaints_ws.append_row(sanitize_row(headers), value_input_option='USER_ENTERED')

            field_map = {
                'Complaint ID': complaint_id,
                'Timestamp': now_str,
                'Complainant Email': complainant_email,
                'Employee Name': employee_name,
                'Complain Date': complain_date,
                'Customer Name': customer_name,
                'Invoice No.': invoice_no,
                'Complain Types': complain_types,
                'Priority': priority,
                'Remarks': remarks,
                'Complain Details': complain_details,
                'Customer Mobile No.': customer_mobile_no,
                'Department Name': department_name,
                'Category': category,
                'Vendor Name': vendor_name,
                'Salesperson Name': salesperson_name,
                'Status': status,
                'Closing Date': closing_date,
                'Solution Given': solution_given,
                'Handled By': handled_by,
                'Total Time Taken': total_time_taken,
                'Comments': comments,
                'GR Amount': gr_amount,
                'GR Expected Date': gr_expected_date,
                'Attachment Link': attachment_link,
                'Last Updated At': now_str,
                'From Department': from_department
            }
            row = [field_map.get(h, '') for h in headers]
            execute_with_retry(lambda: complaints_ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Complaints')

            # 2. Write initial Status History entry
            cls.add_status_history(
                complaint_id=complaint_id,
                old_status='-',
                new_status=status,
                changed_by_name=customer_name,
                changed_by_email=complainant_email,
                remarks="Complaint lodged in system."
            )

            record_dict = dict(zip(SHEET_SCHEMAS['Complaints'], row))
            return cls._normalize_complaint_record(record_dict)

    @classmethod
    def get_complaint_by_id(cls, complaint_id):
        """Returns single complaint dict with all 21 fields, comments, and history."""
        if not complaint_id:
            return None
        target = complaint_id.strip()
        complaints = cls._read_records('Complaints', use_cache=False)
        for c in complaints:
            if c.get('Complaint ID', '').strip() == target:
                return cls._normalize_complaint_record(c)
        return None

    @classmethod
    def get_complaints_for_user(cls, user_email, role='User', user_departments=None, user_dict=None):
        """
        Returns complaints visible to the user respecting RBAC:
        - Super Admin & Admin (ALL scope): All records
        - Department Head & Staff (DEPARTMENT scope): Records in assigned departments, handled by them, or owned
        - User (OWN scope): complaints where Complainant Email == user_email
        """
        complaints = cls._read_records('Complaints', use_cache=True)
        normalized = [cls._normalize_complaint_record(c) for c in complaints]

        user = dict(user_dict) if user_dict else {
            'email': user_email,
            'role': role,
            'department': user_departments or ''
        }
        try:
            custom_roles = cls.get_all_roles()
        except Exception:
            custom_roles = None

        filtered = RBACService.filter_complaints_by_scope(normalized, user, custom_roles=custom_roles)
        return list(reversed(filtered))

    @classmethod
    def update_complaint(cls, complaint_id, data, changed_by_user):
        """
        Updates an existing complaint record by complaint_id with provided data.
        Writes audit trail to Status History if status changed or update remarks provided.
        """
        with cls._db_lock:
            ws = cls._get_worksheet('Complaints')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Complaint ID') + 1 if 'Complaint ID' in headers else 1

            found_row = None
            old_row_data = {}
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) >= id_idx and r[id_idx - 1].strip() == complaint_id.strip():
                    found_row = r_idx
                    for i, h in enumerate(headers):
                        old_row_data[h] = r[i] if i < len(r) else ''
                    break

            if not found_row:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            # Build updated row dictionary
            updated_dict = dict(old_row_data)

            # Map legacy incoming keys to canonical keys
            legacy_mappings = {
                'subject': 'Complain Types',
                'description': 'Complain Details',
                'department': 'Department Name',
                'complainant_name': 'Customer Name',
                'assigned_to_name': 'Handled By',
                'from_department': 'From Department'
            }
            clean_input = dict(data)
            for legacy_k, canon_k in legacy_mappings.items():
                if legacy_k in clean_input and canon_k not in clean_input:
                    clean_input[canon_k] = clean_input[legacy_k]

            for k, v in clean_input.items():
                # Locate header case-insensitively or with underscores
                matched_header = None
                for h in headers:
                    if h.lower() == k.lower() or h.lower().replace(' ', '_') == k.lower():
                        matched_header = h
                        break
                if matched_header and matched_header not in ('Complaint ID', 'Timestamp'):
                    updated_dict[matched_header] = str(v).strip()

            # Validate From Department
            if 'From Department' in updated_dict and updated_dict['From Department']:
                if updated_dict['From Department'] not in ('Online', 'Offline', 'Packing', 'None'):
                    updated_dict['From Department'] = 'None'

            # Format Priority and Status properly
            if 'Priority' in updated_dict and updated_dict['Priority']:
                updated_dict['Priority'] = updated_dict['Priority'].capitalize()

            # Auto calculate total time taken if empty but dates are present
            complain_d = updated_dict.get('Complain Date', '')
            closing_d = updated_dict.get('Closing Date', '')
            if closing_d and complain_d and not clean_input.get('Total Time Taken') and not clean_input.get('total_time_taken'):
                try:
                    d1 = datetime.datetime.strptime(complain_d, '%Y-%m-%d')
                    d2 = datetime.datetime.strptime(closing_d, '%Y-%m-%d')
                    diff_days = (d2 - d1).days
                    updated_dict['Total Time Taken'] = f"{max(0, diff_days)} day{'s' if diff_days != 1 else ''}"
                except Exception:
                    pass

            updated_dict['Last Updated At'] = now_str

            # Construct row adhering to exact sheet column order
            new_row = []
            for h in headers:
                new_row.append(updated_dict.get(h, ''))

            execute_with_retry(lambda: ws.update(f'A{found_row}', [sanitize_row(new_row)], value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Complaints')

            # Check if Status changed or remarks provided, and record in Status History
            old_status = old_row_data.get('Status', '')
            new_status = updated_dict.get('Status', old_status)
            remarks = (
                clean_input.get('remarks') or clean_input.get('Remarks') or
                f"Complaint updated by {changed_by_user.get('role', 'Staff')}."
            )

            if old_status != new_status or clean_input.get('remarks'):
                cls.add_status_history(
                    complaint_id=complaint_id,
                    old_status=old_status or 'Submitted',
                    new_status=new_status,
                    changed_by_name=changed_by_user.get('name') or changed_by_user.get('Full Name', 'Staff'),
                    changed_by_email=changed_by_user.get('email') or changed_by_user.get('Email', ''),
                    remarks=remarks
                )

            return cls._normalize_complaint_record(updated_dict)

    @classmethod
    def delete_complaint(cls, complaint_id, deleted_by_user):
        """
        Deletes a complaint row from the Complaints worksheet.
        Logs an audit trail entry in Status History before deletion.
        """
        with cls._db_lock:
            ws = cls._get_worksheet('Complaints')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Complaint ID') + 1 if 'Complaint ID' in headers else 1
            status_idx = headers.index('Status') + 1 if 'Status' in headers else -1

            found_row = None
            old_status = 'Submitted'
            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) >= id_idx and r[id_idx - 1].strip() == complaint_id.strip():
                    found_row = r_idx
                    if status_idx > 0 and len(r) >= status_idx:
                        old_status = r[status_idx - 1]
                    break

            if not found_row:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            # Record audit trail in Status History before deleting row
            user_name = deleted_by_user.get('name') or deleted_by_user.get('Full Name', 'Admin')
            user_email = deleted_by_user.get('email') or deleted_by_user.get('Email', '')
            user_role = deleted_by_user.get('role') or deleted_by_user.get('Role', 'Admin')

            cls.add_status_history(
                complaint_id=complaint_id,
                old_status=old_status,
                new_status='DELETED',
                changed_by_name=user_name,
                changed_by_email=user_email,
                remarks=f"Complaint permanently deleted by {user_role} ({user_email})."
            )

            execute_with_retry(lambda: ws.delete_rows(found_row))
            cls._invalidate_cache('Complaints')
            return True

    @classmethod
    def update_complaint_status(cls, complaint_id, new_status, changed_by_user, remarks=''):
        """
        Updates complaint status, writes to Status History, and records closing date if applicable.
        """
        with cls._db_lock:
            ws = cls._get_worksheet('Complaints')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Complaint ID') + 1
            status_idx = headers.index('Status') + 1
            updated_idx = headers.index('Last Updated At') + 1 if 'Last Updated At' in headers else -1

            # Check both canonical and legacy column names for closing/resolution
            closing_idx = (
                headers.index('Closing Date') + 1 if 'Closing Date' in headers
                else (headers.index('Resolved At') + 1 if 'Resolved At' in headers else -1)
            )
            solution_idx = (
                headers.index('Solution Given') + 1 if 'Solution Given' in headers
                else (headers.index('Resolution Summary') + 1 if 'Resolution Summary' in headers else -1)
            )

            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            today_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
            found_row = None
            old_status = ''

            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) >= id_idx and r[id_idx - 1].strip() == complaint_id.strip():
                    found_row = r_idx
                    old_status = r[status_idx - 1] if len(r) >= status_idx else ''
                    break

            if not found_row:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            current_row = list(rows[found_row - 1])
            while len(current_row) < len(headers):
                current_row.append('')
            current_row[status_idx - 1] = sanitize_cell_value(new_status)
            if updated_idx > 0:
                current_row[updated_idx - 1] = sanitize_cell_value(now_str)

            if new_status.lower() in ('resolved', 'closed'):
                if closing_idx > 0:
                    current_row[closing_idx - 1] = sanitize_cell_value(today_str)
                if remarks and solution_idx > 0:
                    current_row[solution_idx - 1] = sanitize_cell_value(remarks)

            execute_with_retry(lambda: ws.update(f'A{found_row}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Complaints')

            # Add Status History
            cls.add_status_history(
                complaint_id=complaint_id,
                old_status=old_status or 'Under Review',
                new_status=new_status,
                changed_by_name=changed_by_user.get('name') or changed_by_user.get('Full Name', 'Staff'),
                changed_by_email=changed_by_user.get('email') or changed_by_user.get('Email', ''),
                remarks=remarks
            )

            return True

    @classmethod
    def assign_complaint(cls, complaint_id, assigned_to_name, assigned_to_email, assigned_by_user):
        """Assigns a complaint to a support team member."""
        with cls._db_lock:
            ws = cls._get_worksheet('Complaints')
            rows = execute_with_retry(lambda: ws.get_all_values())
            if not rows or len(rows) < 2:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            headers = [h.strip() for h in rows[0]]
            id_idx = headers.index('Complaint ID') + 1
            handled_by_idx = (
                headers.index('Handled By') + 1 if 'Handled By' in headers
                else (headers.index('Assigned To Name') + 1 if 'Assigned To Name' in headers else -1)
            )
            assignee_email_idx = headers.index('Assigned To Email') + 1 if 'Assigned To Email' in headers else -1
            updated_idx = headers.index('Last Updated At') + 1 if 'Last Updated At' in headers else -1
            status_idx = headers.index('Status') + 1 if 'Status' in headers else -1

            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            found_row = None
            old_status = ''

            for r_idx, r in enumerate(rows[1:], start=2):
                if len(r) >= id_idx and r[id_idx - 1].strip() == complaint_id.strip():
                    found_row = r_idx
                    old_status = r[status_idx - 1] if len(r) >= status_idx else ''
                    break

            if not found_row:
                raise ValueError(f"Complaint '{complaint_id}' not found.")

            current_row = list(rows[found_row - 1])
            while len(current_row) < len(headers):
                current_row.append('')
            if handled_by_idx > 0:
                current_row[handled_by_idx - 1] = sanitize_cell_value(assigned_to_name)
            if assignee_email_idx > 0:
                current_row[assignee_email_idx - 1] = sanitize_cell_value(assigned_to_email)
            if updated_idx > 0:
                current_row[updated_idx - 1] = sanitize_cell_value(now_str)

            # Automatically transition status to 'Under Review' or 'In Progress' if currently 'Submitted'
            if old_status.lower() == 'submitted' and status_idx > 0:
                current_row[status_idx - 1] = sanitize_cell_value('Under Review')
                cls.add_status_history(
                    complaint_id=complaint_id,
                    old_status=old_status,
                    new_status='Under Review',
                    changed_by_name=assigned_by_user.get('name') or assigned_by_user.get('Full Name', 'Staff'),
                    changed_by_email=assigned_by_user.get('email') or assigned_by_user.get('Email', ''),
                    remarks=f"Assigned to {assigned_to_name}."
                )

            execute_with_retry(lambda: ws.update(f'A{found_row}', [sanitize_row(current_row)], value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Complaints')

            # Add Assignment comment
            cls.add_comment(
                complaint_id=complaint_id,
                author_name=assigned_by_user.get('name') or assigned_by_user.get('Full Name', 'Staff'),
                author_email=assigned_by_user.get('email') or assigned_by_user.get('Email', ''),
                author_role=assigned_by_user.get('role') or assigned_by_user.get('Role', 'Admin'),
                comment_type='Internal Note',
                message=f"Complaint assigned to {assigned_to_name} ({assigned_to_email})."
            )

            return True

    # =========================================================================
    # STATUS HISTORY & COMMENTS
    # =========================================================================

    @classmethod
    def add_status_history(cls, complaint_id, old_status, new_status,
                           changed_by_name, changed_by_email, remarks=''):
        """Appends a new entry to the Status History sheet."""
        history_id = f"HST-{uuid.uuid4().hex[:6].upper()}"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        row = [
            history_id,
            complaint_id,
            now_str,
            old_status,
            new_status,
            changed_by_name,
            changed_by_email,
            remarks
        ]

        ws = cls._get_worksheet('Status History')
        execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
        cls._invalidate_cache('Status History')

    @classmethod
    def get_status_history(cls, complaint_id):
        """Retrieves chronological status history for a complaint."""
        history = cls._read_records('Status History', use_cache=True)
        return [h for h in history if h.get('Complaint ID', '').strip() == complaint_id.strip()]

    @classmethod
    def add_comment(cls, complaint_id, author_name, author_email, author_role,
                    comment_type='Public Update', message=''):
        """Appends a comment / activity note for a complaint."""
        comment_id = f"CMT-{uuid.uuid4().hex[:6].upper()}"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        row = [
            comment_id,
            complaint_id,
            now_str,
            author_name,
            author_email,
            author_role,
            comment_type,
            message.strip()
        ]

        ws = cls._get_worksheet('Assignments and Comments')
        execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
        cls._invalidate_cache('Assignments and Comments')

        return {
            'Comment ID': comment_id,
            'Complaint ID': complaint_id,
            'Timestamp': now_str,
            'Author Name': author_name,
            'Author Email': author_email,
            'Author Role': author_role,
            'Comment Type': comment_type,
            'Message': message.strip()
        }

    @classmethod
    def get_comments(cls, complaint_id, user_role='User', user_dict=None):
        """
        Retrieves comments for a complaint.
        Regular users only see 'Public Update' comments.
        Staff and Admins see both 'Public Update' and 'Internal Note' comments.
        """
        all_comments = cls._read_records('Assignments and Comments', use_cache=True)
        relevant = [c for c in all_comments if c.get('Complaint ID', '').strip() == complaint_id.strip()]

        can_view_internal = False
        canon_role = RBACService.canonical_role(user_dict.get('role') or user_dict.get('Role')) if user_dict else RBACService.canonical_role(user_role)
        if canon_role in ('Super Admin', 'Admin', 'Department Head', 'Department Staff'):
            can_view_internal = True
        elif user_dict:
            try:
                custom_roles = cls.get_all_roles()
            except Exception:
                custom_roles = None
            can_view_internal = RBACService.has_permission(user_dict, 'comments:add_internal', custom_roles=custom_roles)

        if not can_view_internal:
            return [c for c in relevant if c.get('Comment Type') != 'Internal Note']
        return relevant

    # =========================================================================
    # SYSTEM CONFIGURATION & OAUTH TOKEN PERSISTENCE
    # =========================================================================

    @classmethod
    def get_system_config(cls, key, default=None):
        """
        Retrieves a persistent configuration value from the System_Config sheet.
        Automatically decrypts sensitive OAuth secrets and tokens using the Vercel encryption key.
        Uses in-memory cache to ensure high-performance in serverless execution.
        """
        try:
            records = cls._read_records('System_Config', use_cache=True)
            for r in records:
                if str(r.get('Key', '')).strip().lower() == str(key).strip().lower():
                    val = r.get('Value')
                    if val is not None and val != '':
                        if is_sensitive_key(key):
                            return decrypt_secret(val)
                        return val
                    return default
        except Exception as e:
            logger.debug(f"Error reading system config for key '{key}': {e}")
        return default

    @classmethod
    def get_all_system_config(cls, mask_sensitive=True):
        """
        Returns all key-value pairs stored in System_Config as a dictionary.
        Sensitive values are masked by default to prevent leaking credentials to diagnostics.
        """
        try:
            records = cls._read_records('System_Config', use_cache=True)
            config_map = {}
            for r in records:
                k = str(r.get('Key', '')).strip()
                if k:
                    raw_val = r.get('Value', '')
                    if is_sensitive_key(k):
                        if mask_sensitive:
                            config_map[k] = mask_secret(decrypt_secret(raw_val)) if raw_val else ''
                        else:
                            config_map[k] = decrypt_secret(raw_val) if raw_val else ''
                    else:
                        config_map[k] = raw_val
            return config_map
        except Exception as e:
            logger.debug(f"Error reading all system config: {e}")
            return {}

    @classmethod
    def set_system_config(cls, key, value, updated_by='system'):
        """
        Saves or updates a persistent configuration value in the System_Config sheet.
        Automatically encrypts sensitive OAuth secrets and refresh tokens before writing to Google Sheets.
        Persists across Vercel serverless deployments without local filesystem dependence.
        """
        clean_key = str(key).strip()
        raw_val = '' if value is None else str(value).strip()

        # Encrypt sensitive keys before saving to Google Sheets
        if is_sensitive_key(clean_key) and raw_val:
            str_val = encrypt_secret(raw_val)
        else:
            str_val = raw_val

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        ws = cls._get_worksheet('System_Config')
        cell_match = None
        try:
            cell_match = execute_with_retry(lambda: ws.find(clean_key))
        except Exception:
            cell_match = None

        if cell_match and cell_match.col == 1:
            row_idx = cell_match.row
            row_data = [sanitize_cell_value(clean_key), sanitize_cell_value(str_val), now_str, updated_by]
            execute_with_retry(lambda: ws.update(f"A{row_idx}:D{row_idx}", [row_data], value_input_option='USER_ENTERED'))
        else:
            row_data = [sanitize_cell_value(clean_key), sanitize_cell_value(str_val), now_str, updated_by]
            execute_with_retry(lambda: ws.append_row(row_data, value_input_option='USER_ENTERED'))

        cls._invalidate_cache('System_Config')
        return True

    @classmethod
    def delete_system_config(cls, key):
        """Deletes a key from the System_Config sheet."""
        clean_key = str(key).strip()
        ws = cls._get_worksheet('System_Config')
        try:
            cell_match = execute_with_retry(lambda: ws.find(clean_key))
            if cell_match and cell_match.col == 1:
                execute_with_retry(lambda: ws.delete_rows(cell_match.row))
                cls._invalidate_cache('System_Config')
                return True
        except Exception as e:
            logger.warning(f"Error deleting system config key '{key}': {e}")
        return False
