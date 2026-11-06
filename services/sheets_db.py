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
        'Attachment Link', 'Last Updated At'
    ],
    'Users': [
        'User ID', 'Full Name', 'Email', 'Password Hash', 'Role',
        'Department', 'Is Active', 'Created At', 'Last Login'
    ],
    'Departments': [
        'Department ID', 'Name', 'Description', 'Lead Email', 'Is Active', 'Created At'
    ],
    'Status History': [
        'History ID', 'Complaint ID', 'Timestamp', 'Old Status', 'New Status',
        'Changed By Name', 'Changed By Email', 'Remarks'
    ],
    'Assignments and Comments': [
        'Comment ID', 'Complaint ID', 'Timestamp', 'Author Name',
        'Author Email', 'Author Role', 'Comment Type', 'Message'
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

        # Bootstrap seed data if Users or Departments are empty
        cls._bootstrap_initial_data(existing_sheets)

    @classmethod
    def _bootstrap_initial_data(cls, existing_sheets):
        """Seeds initial admin (if configured in env) and default departments if sheets are brand new."""
        # Check Users sheet
        users_ws = existing_sheets.get('Users')
        if users_ws:
            all_users = execute_with_retry(lambda: users_ws.get_all_values())
            if len(all_users) <= 1: # Only header row or empty
                if Config.INITIAL_ADMIN_PASSWORD:
                    admin_email = Config.INITIAL_ADMIN_EMAIL
                    admin_name = Config.INITIAL_ADMIN_NAME
                    admin_pwd_hash = generate_password_hash(Config.INITIAL_ADMIN_PASSWORD)
                    now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    
                    admin_row = [
                        'USR-0001',
                        admin_name,
                        admin_email,
                        admin_pwd_hash,
                        'Super Admin',
                        'Management',
                        'TRUE',
                        now_str,
                        ''
                    ]
                    execute_with_retry(lambda: users_ws.append_row(sanitize_row(admin_row), value_input_option='USER_ENTERED'))
                    logger.info(f"[BOOTSTRAP] Initial Super Admin '{admin_email}' created from environment config.")
                else:
                    logger.info("[BOOTSTRAP] No INITIAL_ADMIN_PASSWORD set; skipping auto-seeding. First-run UI setup enabled.")

        # Check Departments sheet
        dept_ws = existing_sheets.get('Departments')
        if dept_ws:
            all_depts = execute_with_retry(lambda: dept_ws.get_all_values())
            if len(all_depts) <= 1:
                now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                dept_rows = []
                lead_email = Config.INITIAL_ADMIN_EMAIL or 'admin@organization.local'
                for idx, (name, desc) in enumerate(DEFAULT_DEPARTMENTS, start=1):
                    dept_rows.append(sanitize_row([
                        f"DEP-{idx:03d}",
                        name,
                        desc,
                        lead_email,
                        'TRUE',
                        now_str
                    ]))
                for r in dept_rows:
                    execute_with_retry(lambda: dept_ws.append_row(r, value_input_option='USER_ENTERED'))
                logger.info(f"[BOOTSTRAP] Seeded {len(dept_rows)} default departments.")

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
    def create_user(cls, full_name, email, password, role='User', department='General'):
        """Creates a new user with securely hashed password."""
        with cls._db_lock:
            clean_email = email.lower().strip()
            if cls.find_user_by_email(clean_email):
                raise ValueError(f"User with email '{clean_email}' already exists.")

            users = cls._read_records('Users', use_cache=False)
            user_num = len(users) + 1
            user_id = f"USR-{user_num:04d}"
            pwd_hash = generate_password_hash(password)
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            row = [
                user_id,
                full_name.strip(),
                clean_email,
                pwd_hash,
                role,
                department.strip(),
                'TRUE',
                now_str,
                ''
            ]

            ws = cls._get_worksheet('Users')
            execute_with_retry(lambda: ws.append_row(sanitize_row(row), value_input_option='USER_ENTERED'))
            cls._invalidate_cache('Users')

            return {
                'User ID': user_id,
                'Full Name': full_name.strip(),
                'Email': clean_email,
                'Role': role,
                'Department': department.strip(),
                'Is Active': 'TRUE',
                'Created At': now_str
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

        # Ensure all 21 canonical fields are present
        canonical_fields = [
            'Employee Name', 'Complain Date', 'Customer Name', 'Invoice No.',
            'Complain Types', 'Priority', 'Remarks', 'Complain Details',
            'Customer Mobile No.', 'Department Name', 'Category', 'Vendor Name',
            'Salesperson Name', 'Status', 'Closing Date', 'Solution Given',
            'Handled By', 'Total Time Taken', 'Comments', 'GR Amount',
            'GR Expected Date'
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

            row = [
                complaint_id,
                now_str,              # Timestamp
                complainant_email,    # Complainant Email
                employee_name,        # Employee Name
                complain_date,        # Complain Date
                customer_name,        # Customer Name
                invoice_no,           # Invoice No.
                complain_types,       # Complain Types
                priority,             # Priority
                remarks,              # Remarks
                complain_details,     # Complain Details
                customer_mobile_no,   # Customer Mobile No.
                department_name,      # Department Name
                category,             # Category
                vendor_name,          # Vendor Name
                salesperson_name,     # Salesperson Name
                status,               # Status
                closing_date,         # Closing Date
                solution_given,       # Solution Given
                handled_by,           # Handled By
                total_time_taken,     # Total Time Taken
                comments,             # Comments
                gr_amount,            # GR Amount
                gr_expected_date,     # GR Expected Date
                attachment_link,      # Attachment Link
                now_str               # Last Updated At
            ]

            # 1. Write Complaint record to Complaints worksheet
            complaints_ws = cls._get_worksheet('Complaints')
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
    def get_complaints_for_user(cls, user_email, role='User'):
        """
        Returns complaints visible to the user:
        - Regular User: only complaints where Complainant Email == user_email
        - Staff / Admin: all complaints
        """
        complaints = cls._read_records('Complaints', use_cache=True)
        normalized = [cls._normalize_complaint_record(c) for c in complaints]
        if role in ('Super Admin', 'Admin', 'Support Staff'):
            return list(reversed(normalized))

        clean_email = user_email.lower().strip()
        filtered = [c for c in normalized if c.get('Complainant Email', '').lower().strip() == clean_email]
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
                'assigned_to_name': 'Handled By'
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
    def get_comments(cls, complaint_id, user_role='User'):
        """
        Retrieves comments for a complaint.
        Regular users only see 'Public Update' comments.
        Staff and Admins see both 'Public Update' and 'Internal Note' comments.
        """
        all_comments = cls._read_records('Assignments and Comments', use_cache=True)
        relevant = [c for c in all_comments if c.get('Complaint ID', '').strip() == complaint_id.strip()]
        if user_role not in ('Super Admin', 'Admin', 'Support Staff'):
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
