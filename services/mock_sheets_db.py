import time
import uuid
import datetime
from werkzeug.security import generate_password_hash
from config import Config
from services.google_client import sanitize_cell_value
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

DEFAULT_DEPARTMENTS = [
    ('IT & Technical Support', 'Hardware, software, network, email, and systems assistance.'),
    ('Facilities & Infrastructure', 'Building maintenance, electrical, plumbing, hygiene, and utilities.'),
    ('Finance & Billing', 'Salary, invoice processing, reimbursement, and billing inquiries.'),
    ('Human Resources', 'Workplace culture, policy guidance, onboarding, and employee relations.'),
    ('Customer Operations', 'Customer-facing services, service delivery, and logistics complaints.')
]

class MockSheetsDB:
    @classmethod
    def generate_complaint_id(cls):
        """Generates a unique complaint ID."""
        date_part = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')
        random_suffix = uuid.uuid4().hex[:4].upper()
        return f"CMP-{date_part}-{random_suffix}"

    @classmethod
    def get_active_departments(cls):
        """Returns active departments list."""
        return [d for d in cls._store['Departments'] if str(d.get('Is Active', 'TRUE')).upper() in ('TRUE', '1', 'YES')]

    @classmethod
    def create_complaint(cls, complaint_dict, current_user=None):
        """Creates a complaint record and returns it."""
        res = cls.bulk_create_complaints([complaint_dict], current_user or {})
        return res[0] if res else {}

    """
    Isolated In-Memory mock storage implementing the exact same database API as SheetsDB.
    Strictly used for offline unit testing, test suites, and mock verification.
    """
    _store = {
        'Complaints': [],
        'Users': [],
        'Departments': [],
        'Complaint_Types': [],
        'Salespersons': [],
        'Status History': [],
        'Assignments and Comments': [],
        'System_Config': [],
        'Roles': [],
        'Audit_Logs': []
    }

    @classmethod
    def reset(cls):
        """Resets in-memory store to clean initial state with bootstrap admin, roles, and departments."""
        cls._store = {
            'Complaints': [],
            'Users': [],
            'Departments': [],
            'Complaint_Types': [],
            'Salespersons': [],
            'Status History': [],
            'Assignments and Comments': [],
            'System_Config': [],
            'Roles': [],
            'Audit_Logs': []
        }
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        # Bootstrap system default roles
        for rname, rtmpl in DEFAULT_ROLE_TEMPLATES.items():
            cls._store['Roles'].append({
                'Role ID': rtmpl['id'],
                'Name': rtmpl['name'],
                'Description': rtmpl['description'],
                'Data Scope': rtmpl['data_scope'],
                'Permissions': ','.join(rtmpl['permissions']),
                'Is System Role': 'TRUE' if rtmpl.get('is_system_role') else 'FALSE',
                'Created At': now_str
            })

        # Bootstrap default admin if configured in environment
        if Config.INITIAL_ADMIN_PASSWORD:
            cls._store['Users'].append({
                'User ID': 'USR-0001',
                'Full Name': Config.INITIAL_ADMIN_NAME,
                'Email': Config.INITIAL_ADMIN_EMAIL,
                'Password Hash': generate_password_hash(Config.INITIAL_ADMIN_PASSWORD),
                'Role': 'Super Admin',
                'Department': 'Management',
                'Is Active': 'TRUE',
                'Created At': now_str,
                'Last Login': '',
                'Custom Permissions': ''
            })
        # Bootstrap default departments
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        lead_email = Config.INITIAL_ADMIN_EMAIL or 'admin@organization.local'
        for idx, (name, desc) in enumerate(DEFAULT_DEPARTMENTS, start=1):
            cls._store['Departments'].append({
                'Department ID': f"DEP-{idx:03d}",
                'Name': name,
                'Description': desc,
                'Lead Email': lead_email,
                'Is Active': 'TRUE',
                'Created At': now_str
            })
        # Bootstrap default complaint types
        default_types = [
            ('Damage', 'Damaged or defective merchandise upon arrival or unboxing'),
            ('Product Defect', 'Manufacturing defect, stitching, material or fabric flaw'),
            ('Wrong Item Delivered', 'Incorrect size, color, or variant sent to customer'),
            ('Transit Damage', 'Item damaged during courier or cargo transportation'),
            ('Billing Discrepancy', 'Invoice amount, discount, or tax calculation error'),
            ('Delivery Delay', 'Delayed shipment beyond promised delivery date'),
            ('Quality Issue', 'Fabric or finishing not meeting expected standards'),
            ('Other', 'Other customer issues or general service complaints')
        ]
        for idx, (name, desc) in enumerate(default_types, start=1):
            cls._store['Complaint_Types'].append({
                'Type ID': f"CT-{idx:03d}",
                'Name': name,
                'Description': desc,
                'Is Active': 'TRUE',
                'Created At': now_str
            })
        # Bootstrap default salespersons
        default_salespersons = [
            ('Vikram Rao', '+91 98765 00001', 'vikram.rao@company.local'),
            ('Priya Sharma', '+91 98765 00002', 'priya.sharma@company.local'),
            ('Rahul Verma', '+91 98765 00003', 'rahul.verma@company.local'),
            ('Direct Store Sales', '', '')
        ]
        for idx, (name, phone, email) in enumerate(default_salespersons, start=1):
            cls._store['Salespersons'].append({
                'Salesperson ID': f"SP-{idx:03d}",
                'Name': name,
                'Phone': phone,
                'Email': email,
                'Is Active': 'TRUE',
                'Created At': now_str
            })

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
    def find_user_by_email(cls, email):
        if not email: return None
        target = email.lower().strip()
        for u in cls._store['Users']:
            if u.get('Email', '').lower().strip() == target:
                return dict(u)
        return None

    @classmethod
    def find_user_by_id(cls, user_id):
        if not user_id: return None
        for u in cls._store['Users']:
            if u.get('User ID', '').strip() == str(user_id).strip():
                return dict(u)
        return None

    @classmethod
    def get_all_users(cls):
        users = []
        for u in cls._store['Users']:
            copy = dict(u)
            copy.pop('Password Hash', None)
            users.append(copy)
        return users

    @classmethod
    def create_user(cls, full_name, email, password, role='User', department='General', custom_permissions=None):
        clean_email = email.lower().strip()
        if cls.find_user_by_email(clean_email):
            raise ValueError(f"User with email '{clean_email}' already exists.")

        canon_role = RBACService.canonical_role(role)
        all_roles = cls.get_all_roles()
        role_names = [r['Name'].strip().lower() for r in all_roles]
        if canon_role.lower() not in role_names and canon_role.lower() not in [r.lower() for r in DEFAULT_ROLE_TEMPLATES.keys()]:
            raise ValueError(f"Invalid role '{role}'.")

        dept_val = ', '.join([d.strip() for d in department if d.strip()]) if isinstance(department, list) else (department.strip() if department else 'General')
        cust_perms_str = ', '.join([p.strip() for p in custom_permissions if p.strip()]) if isinstance(custom_permissions, list) else (str(custom_permissions).strip() if custom_permissions else '')

        user_num = len(cls._store['Users']) + 1
        user_id = f"USR-{user_num:04d}"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        record = {
            'User ID': user_id,
            'Full Name': full_name.strip(),
            'Email': clean_email,
            'Password Hash': generate_password_hash(password),
            'Role': canon_role,
            'Department': dept_val,
            'Is Active': 'TRUE',
            'Created At': now_str,
            'Last Login': '',
            'Custom Permissions': cust_perms_str
        }
        cls._store['Users'].append(record)
        safe = dict(record)
        safe.pop('Password Hash', None)
        return safe

    @classmethod
    def update_user_last_login(cls, email):
        clean_email = email.lower().strip()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for u in cls._store['Users']:
            if u.get('Email', '').lower().strip() == clean_email:
                u['Last Login'] = now_str
                break

    @classmethod
    def count_active_super_admins(cls):
        return sum(1 for u in cls._store['Users'] if u.get('Role') == 'Super Admin' and str(u.get('Is Active', '')).upper() == 'TRUE')

    @classmethod
    def update_user(cls, user_id, full_name=None, email=None, role=None, department=None, is_active=None, new_password=None, custom_permissions=None):
        user = None
        old_email = None
        clean_email = email.lower().strip() if email else None

        for u in cls._store['Users']:
            if u.get('User ID', '').strip() == str(user_id).strip():
                user = u
                old_email = u.get('Email', '').lower()
            elif clean_email and u.get('Email', '').lower().strip() == clean_email:
                raise ValueError(f"Email '{clean_email}' is already in use by another user.")

        if not user:
            raise ValueError(f"User '{user_id}' not found.")

        if full_name is not None and full_name.strip():
            user['Full Name'] = full_name.strip()
        if clean_email:
            user['Email'] = clean_email
        if role is not None and role.strip():
            canon_role = RBACService.canonical_role(role)
            all_roles = cls.get_all_roles()
            role_names = [r['Name'].strip().lower() for r in all_roles]
            if canon_role.lower() not in role_names and canon_role.lower() not in [r.lower() for r in DEFAULT_ROLE_TEMPLATES.keys()]:
                raise ValueError(f"Invalid role '{role}'.")
            user['Role'] = canon_role
        if department is not None:
            dept_val = ', '.join([d.strip() for d in department if d.strip()]) if isinstance(department, list) else (department.strip() if department else 'General')
            user['Department'] = dept_val
        if custom_permissions is not None:
            user['Custom Permissions'] = ', '.join([p.strip() for p in custom_permissions if p.strip()]) if isinstance(custom_permissions, list) else str(custom_permissions).strip()
        if is_active is not None:
            val_upper = str(is_active).strip().upper()
            if val_upper in ('ARCHIVED', 'ARCHIVE'):
                user['Is Active'] = 'ARCHIVED'
            elif val_upper in ('TRUE', '1', 'YES'):
                user['Is Active'] = 'TRUE'
            else:
                user['Is Active'] = 'FALSE'
        if new_password and str(new_password).strip():
            if len(str(new_password).strip()) < 6:
                raise ValueError("Password must be at least 6 characters.")
            user['Password Hash'] = generate_password_hash(str(new_password).strip())

        # Cascade email to complaints if changed
        if clean_email and old_email and clean_email != old_email:
            for c in cls._store['Complaints']:
                if c.get('Complainant Email', '').lower() == old_email:
                    c['Complainant Email'] = clean_email

        safe = dict(user)
        safe.pop('Password Hash', None)
        return safe

    @classmethod
    def get_user_usage(cls, user_id_or_email):
        user = cls.find_user_by_id(user_id_or_email) or cls.find_user_by_email(user_id_or_email)
        if not user:
            return None

        user_email = (user.get('Email') or '').lower().strip()
        user_name = (user.get('Full Name') or '').strip().lower()

        lodged_count = 0
        handled_count = 0
        for c in cls._store.get('Complaints', []):
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

        comment_count = 0
        for com in cls._store.get('Assignments and Comments', []):
            author_email = (com.get('Author Email') or '').lower().strip()
            author_name = (com.get('Author Name') or '').strip().lower()
            if (user_email and author_email == user_email) or (user_name and author_name == user_name):
                comment_count += 1

        history_count = 0
        for h in cls._store.get('Status History', []):
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
        for i, u in enumerate(cls._store.get('Users', [])):
            if u.get('User ID', '').strip() == str(user_id).strip():
                del cls._store['Users'][i]
                return True
        raise ValueError(f"User '{user_id}' not found.")

    # =========================================================================
    # ROLE MANAGEMENT (MOCK)
    # =========================================================================

    @classmethod
    def get_all_roles(cls):
        """Returns all role configurations from the in-memory Roles store."""
        roles = []
        for r in cls._store.get('Roles', []):
            rcopy = dict(r)
            perms_raw = rcopy.get('Permissions', '')
            if isinstance(perms_raw, str):
                rcopy['Permissions'] = [p.strip() for p in perms_raw.split(',') if p.strip()]
            roles.append(rcopy)
        return roles

    @classmethod
    def get_role_by_id(cls, role_id):
        if not role_id:
            return None
        target = str(role_id).strip()
        for r in cls.get_all_roles():
            if str(r.get('Role ID', '')).strip() == target:
                return r
        return None

    @classmethod
    def get_role_by_name(cls, name):
        if not name:
            return None
        target = str(name).strip().lower()
        for r in cls.get_all_roles():
            if str(r.get('Name', '')).strip().lower() == target:
                return r
        return None

    @classmethod
    def create_role(cls, name, description='', data_scope='DEPARTMENT', permissions=None):
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Role name is required.")

        if cls.get_role_by_name(clean_name):
            raise ValueError(f"Role '{clean_name}' already exists.")

        scope_val = str(data_scope or 'DEPARTMENT').strip().upper()
        if scope_val not in ('ALL', 'DEPARTMENT', 'OWN'):
            scope_val = 'DEPARTMENT'

        perms_list = [p.strip() for p in permissions if p.strip()] if isinstance(permissions, list) else ([p.strip() for p in str(permissions).split(',') if p.strip()] if permissions else [])
        perms_str = ', '.join(sorted(set(perms_list)))

        role_num = len(cls._store.get('Roles', [])) + 1
        role_id = f"ROLE-{role_num:03d}"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        record = {
            'Role ID': role_id,
            'Name': clean_name,
            'Description': str(description or '').strip(),
            'Data Scope': scope_val,
            'Permissions': perms_str,
            'Is System Role': 'FALSE',
            'Created At': now_str
        }
        cls._store['Roles'].append(record)
        return {
            'Role ID': role_id,
            'Name': clean_name,
            'Description': record['Description'],
            'Data Scope': scope_val,
            'Permissions': [p.strip() for p in perms_str.split(',') if p.strip()],
            'Is System Role': 'FALSE',
            'Created At': now_str
        }

    @classmethod
    def update_role(cls, role_id, name=None, description=None, data_scope=None, permissions=None):
        role_record = None
        for r in cls._store.get('Roles', []):
            if r.get('Role ID', '').strip() == str(role_id).strip():
                role_record = r
                break

        if not role_record:
            raise ValueError(f"Role '{role_id}' not found.")

        is_sys = str(role_record.get('Is System Role', 'FALSE')).upper() == 'TRUE'
        old_name = role_record.get('Name', '').strip()

        if name is not None and name.strip():
            clean_name = name.strip()
            if is_sys and clean_name.lower() != old_name.lower():
                raise ValueError("System role names cannot be renamed.")
            if clean_name.lower() != old_name.lower():
                existing = cls.get_role_by_name(clean_name)
                if existing and existing.get('Role ID') != role_id:
                    raise ValueError(f"Role name '{clean_name}' already exists.")
                role_record['Name'] = clean_name
                cls._rename_role_in_users(old_name, clean_name)

        if description is not None:
            role_record['Description'] = str(description).strip()

        if data_scope is not None:
            if is_sys and old_name == 'Super Admin' and str(data_scope).strip().upper() != 'ALL':
                raise ValueError("Super Admin data scope cannot be changed from ALL.")
            scope_val = str(data_scope).strip().upper()
            if scope_val in ('ALL', 'DEPARTMENT', 'OWN'):
                role_record['Data Scope'] = scope_val

        if permissions is not None:
            if is_sys and old_name == 'Super Admin':
                pass  # Super admin keeps all
            else:
                perms_list = [p.strip() for p in permissions if p.strip()] if isinstance(permissions, list) else ([p.strip() for p in str(permissions).split(',') if p.strip()] if permissions else [])
                role_record['Permissions'] = ', '.join(sorted(set(perms_list)))

        perms_raw = role_record.get('Permissions', '')
        return {
            'Role ID': role_record['Role ID'],
            'Name': role_record['Name'],
            'Description': role_record.get('Description', ''),
            'Data Scope': role_record.get('Data Scope', 'DEPARTMENT'),
            'Permissions': [p.strip() for p in perms_raw.split(',') if p.strip()] if isinstance(perms_raw, str) else list(perms_raw),
            'Is System Role': role_record.get('Is System Role', 'FALSE'),
            'Created At': role_record.get('Created At', '')
        }

    @classmethod
    def get_role_usage(cls, role_id_or_name):
        role = cls.get_role_by_id(role_id_or_name) or cls.get_role_by_name(role_id_or_name)
        if not role:
            raise ValueError(f"Role '{role_id_or_name}' not found.")
        target_name = role['Name'].strip().lower()
        users = cls.get_all_users()
        assigned_users = [u for u in users if (u.get('Role') or '').strip().lower() == target_name]
        return role, len(assigned_users), assigned_users

    @classmethod
    def delete_role(cls, role_id):
        role, usage_count, assigned_users = cls.get_role_usage(role_id)
        if str(role.get('Is System Role', 'FALSE')).upper() == 'TRUE':
            raise ValueError(f"Cannot delete system default role '{role['Name']}'.")
        if usage_count > 0:
            user_list_str = ', '.join([u.get('Email', '') for u in assigned_users[:3]])
            raise ValueError(f"Cannot delete role '{role['Name']}': It is currently assigned to {usage_count} user(s) ({user_list_str}). Reassign users first.")

        idx_to_remove = None
        for idx, r in enumerate(cls._store.get('Roles', [])):
            if r.get('Role ID', '').strip() == str(role_id).strip():
                idx_to_remove = idx
                break
        if idx_to_remove is not None:
            del cls._store['Roles'][idx_to_remove]
            return True
        raise ValueError(f"Role '{role_id}' not found.")

    @classmethod
    def _rename_role_in_users(cls, old_name, new_name):
        target = old_name.strip().lower()
        for u in cls._store.get('Users', []):
            if (u.get('Role') or '').strip().lower() == target:
                u['Role'] = new_name

    # =========================================================================
    # AUDIT LOGGING (MOCK)
    # =========================================================================

    @classmethod
    def log_audit_event(cls, user_id='', user_email='', user_role='', action='', module='', record_id='', department='', details='', previous_state='', new_state='', result='SUCCESS', ip_address=''):
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        log_id = f"LOG-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        entry = {
            'Log ID': log_id,
            'User ID': user_id or '',
            'User Email': user_email or '',
            'User Role': user_role or '',
            'Action': action or '',
            'Module': module or '',
            'Record ID': str(record_id or ''),
            'Department': department or '',
            'Details': str(details or ''),
            'Previous State': str(previous_state or ''),
            'New State': str(new_state or ''),
            'Result': result or 'SUCCESS',
            'IP Address': ip_address or '',
            'Timestamp': now_str
        }
        cls._store['Audit_Logs'].append(entry)
        return log_id

    @classmethod
    def get_audit_logs(cls, limit=100, module=None, action=None, user_email=None):
        logs = list(reversed(cls._store.get('Audit_Logs', [])))
        filtered = []
        for l in logs:
            if module and (l.get('Module') or '').lower() != module.lower():
                continue
            if action and (l.get('Action') or '').lower() != action.lower():
                continue
            if user_email and (l.get('User Email') or '').lower() != user_email.lower():
                continue
            filtered.append(dict(l))
            if len(filtered) >= limit:
                break
        return filtered

    @classmethod
    def get_all_departments(cls, include_inactive=False):
        if include_inactive:
            return [dict(d) for d in cls._store['Departments']]
        return [dict(d) for d in cls._store['Departments'] if str(d.get('Is Active', 'TRUE')).upper() == 'TRUE']

    @classmethod
    def get_department_by_id(cls, dept_id):
        if not dept_id: return None
        for d in cls._store['Departments']:
            if d.get('Department ID', '').strip() == dept_id.strip():
                return dict(d)
        return None

    @classmethod
    def get_department_by_name(cls, name):
        if not name: return None
        target = name.strip().lower()
        for d in cls._store['Departments']:
            if d.get('Name', '').strip().lower() == target:
                return dict(d)
        return None

    @classmethod
    def create_department(cls, name, description='', lead_email=''):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Department name is required.")
        for d in cls._store['Departments']:
            if d.get('Name', '').lower() == clean_name.lower():
                raise ValueError(f"Department '{clean_name}' already exists.")
        dept_id = f"DEP-{len(cls._store['Departments']) + 1:03d}"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        dept = {
            'Department ID': dept_id,
            'Name': clean_name,
            'Description': description.strip(),
            'Lead Email': lead_email.strip().lower(),
            'Is Active': 'TRUE',
            'Created At': now_str
        }
        cls._store['Departments'].append(dept)
        return dict(dept)

    @classmethod
    def update_department(cls, dept_id, name, description='', lead_email='', is_active='TRUE'):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Department name is required.")

        target_dept = None
        for d in cls._store['Departments']:
            if d.get('Department ID', '').strip() == dept_id.strip():
                target_dept = d
            elif d.get('Name', '').strip().lower() == clean_name.lower():
                raise ValueError(f"Another department named '{clean_name}' already exists.")

        if not target_dept:
            raise ValueError(f"Department '{dept_id}' not found.")

        old_name = target_dept['Name']
        target_dept['Name'] = clean_name
        target_dept['Description'] = description.strip()
        target_dept['Lead Email'] = lead_email.strip().lower()
        target_dept['Is Active'] = 'TRUE' if str(is_active).upper() in ('TRUE', '1', 'YES') else 'FALSE'

        if old_name != clean_name:
            cls._reassign_department_in_complaints(old_name, clean_name)

        return dict(target_dept)

    @classmethod
    def get_department_usage(cls, dept_id_or_name):
        dept = None
        for d in cls._store['Departments']:
            if d.get('Department ID', '').strip() == dept_id_or_name.strip() or \
               d.get('Name', '').strip().lower() == dept_id_or_name.strip().lower():
                dept = d
                break
        if not dept:
            raise ValueError(f"Department '{dept_id_or_name}' not found.")

        target_name = dept['Name'].strip().lower()
        linked_ids = []
        for c in cls._store['Complaints']:
            raw_dept = (c.get('Department Name') or c.get('Department') or '').strip()
            parts = [p.strip().lower() for p in raw_dept.split(',') if p.strip()]
            if target_name in parts:
                linked_ids.append(c.get('Complaint ID'))

        return dict(dept), len(linked_ids), linked_ids

    @classmethod
    def delete_department(cls, dept_id, reassign_to_dept_id_or_name=None):
        dept, usage_count, linked_ids = cls.get_department_usage(dept_id)
        if usage_count > 0:
            if not reassign_to_dept_id_or_name:
                raise ValueError(
                    f"Cannot delete department '{dept['Name']}': It is currently linked to {usage_count} "
                    f"complaint(s) ({', '.join(linked_ids[:5])}{'...' if len(linked_ids) > 5 else ''}). "
                    f"Please reassign these complaints before deleting this department."
                )

            new_dept = None
            for d in cls._store['Departments']:
                if d.get('Department ID', '').strip() == reassign_to_dept_id_or_name.strip() or \
                   d.get('Name', '').strip().lower() == reassign_to_dept_id_or_name.strip().lower():
                    new_dept = d
                    break
            if not new_dept:
                raise ValueError(f"Replacement department '{reassign_to_dept_id_or_name}' not found.")
            if new_dept['Department ID'] == dept['Department ID']:
                raise ValueError("Cannot reassign complaints to the department being deleted.")

            cls._reassign_department_in_complaints(dept['Name'], new_dept['Name'])

        found_idx = None
        for idx, d in enumerate(cls._store['Departments']):
            if d.get('Department ID', '').strip() == dept_id.strip():
                found_idx = idx
                break

        if found_idx is None:
            raise ValueError(f"Department '{dept_id}' not found.")

        cls._store['Departments'].pop(found_idx)
        return True

    @classmethod
    def _reassign_department_in_complaints(cls, old_name, new_name):
        target_old = old_name.strip().lower()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for c in cls._store['Complaints']:
            raw_val = (c.get('Department Name') or c.get('Department') or '').strip()
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
                c['Department Name'] = ", ".join(new_parts)
                c['Department'] = c['Department Name']
                c['Last Updated At'] = now_str

    # =========================================================================
    # COMPLAINT TYPES MASTER OPERATIONS (MOCK)
    # =========================================================================

    @classmethod
    def get_all_complaint_types(cls, include_inactive=False):
        types = cls._store.get('Complaint_Types', [])
        if include_inactive:
            return [dict(t) for t in types]
        return [dict(t) for t in types if str(t.get('Is Active', 'TRUE')).upper() == 'TRUE']

    @classmethod
    def get_complaint_type_by_id(cls, type_id):
        if not type_id: return None
        for t in cls._store.get('Complaint_Types', []):
            if t.get('Type ID', '').strip() == type_id.strip():
                return dict(t)
        return None

    @classmethod
    def get_complaint_type_by_name(cls, name):
        if not name: return None
        target = name.strip().lower()
        for t in cls._store.get('Complaint_Types', []):
            if t.get('Name', '').strip().lower() == target:
                return dict(t)
        return None

    @classmethod
    def create_complaint_type(cls, name, description=''):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Complaint Type name is required.")
        for t in cls._store.get('Complaint_Types', []):
            if t.get('Name', '').strip().lower() == clean_name.lower():
                raise ValueError(f"Complaint Type '{clean_name}' already exists.")
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        new_id = f"CT-{len(cls._store.get('Complaint_Types', [])) + 1:03d}"
        item = {
            'Type ID': new_id,
            'Name': clean_name,
            'Description': description.strip(),
            'Is Active': 'TRUE',
            'Created At': now_str
        }
        cls._store.setdefault('Complaint_Types', []).append(item)
        return dict(item)

    @classmethod
    def update_complaint_type(cls, type_id, name, description='', is_active='TRUE'):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Complaint Type name is required.")
        found = None
        old_name = None
        for t in cls._store.get('Complaint_Types', []):
            if t.get('Type ID', '').strip() == type_id.strip():
                found = t
                old_name = t.get('Name')
            elif t.get('Name', '').strip().lower() == clean_name.lower():
                raise ValueError(f"Another Complaint Type named '{clean_name}' already exists.")
        if not found:
            raise ValueError(f"Complaint Type '{type_id}' not found.")
        found['Name'] = clean_name
        found['Description'] = description.strip()
        found['Is Active'] = 'TRUE' if str(is_active).upper() in ('TRUE', '1', 'YES') else 'FALSE'
        if old_name and old_name != clean_name:
            cls._rename_complaint_type_in_complaints(old_name, clean_name)
        return dict(found)

    @classmethod
    def get_complaint_type_usage(cls, type_id_or_name):
        ct = None
        for t in cls._store.get('Complaint_Types', []):
            if t.get('Type ID', '').strip() == type_id_or_name.strip() or \
               t.get('Name', '').strip().lower() == type_id_or_name.strip().lower():
                ct = t
                break
        if not ct:
            raise ValueError(f"Complaint Type '{type_id_or_name}' not found.")
        target = ct['Name'].strip().lower()
        linked = [c.get('Complaint ID') for c in cls._store.get('Complaints', []) if (c.get('Complain Types') or c.get('Subject') or '').strip().lower() == target]
        return ct, len(linked), linked

    @classmethod
    def delete_complaint_type(cls, type_id):
        ct, usage_count, linked_ids = cls.get_complaint_type_usage(type_id)
        if usage_count > 0:
            raise ValueError(
                f"Cannot delete Complaint Type '{ct['Name']}': It is currently used in {usage_count} "
                f"complaint(s) ({', '.join(linked_ids[:5])}{'...' if len(linked_ids) > 5 else ''}). "
                f"Please deactivate this complaint type instead to preserve historical records."
            )
        found_idx = None
        for idx, t in enumerate(cls._store.get('Complaint_Types', [])):
            if t.get('Type ID', '').strip() == type_id.strip():
                found_idx = idx
                break
        if found_idx is None:
            raise ValueError(f"Complaint Type '{type_id}' not found.")
        cls._store['Complaint_Types'].pop(found_idx)
        return True

    @classmethod
    def _rename_complaint_type_in_complaints(cls, old_name, new_name):
        target_old = old_name.strip().lower()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for c in cls._store.get('Complaints', []):
            raw = (c.get('Complain Types') or c.get('Subject') or '').strip().lower()
            if raw == target_old:
                c['Complain Types'] = new_name
                c['Subject'] = new_name
                c['Last Updated At'] = now_str

    # =========================================================================
    # SALESPERSONS MASTER OPERATIONS (MOCK)
    # =========================================================================

    @classmethod
    def get_all_salespersons(cls, include_inactive=False):
        salespersons = cls._store.get('Salespersons', [])
        if include_inactive:
            return [dict(s) for s in salespersons]
        return [dict(s) for s in salespersons if str(s.get('Is Active', 'TRUE')).upper() == 'TRUE']

    @classmethod
    def get_salesperson_by_id(cls, sp_id):
        if not sp_id: return None
        for s in cls._store.get('Salespersons', []):
            if s.get('Salesperson ID', '').strip() == sp_id.strip():
                return dict(s)
        return None

    @classmethod
    def get_salesperson_by_name(cls, name):
        if not name: return None
        target = name.strip().lower()
        for s in cls._store.get('Salespersons', []):
            if s.get('Name', '').strip().lower() == target:
                return dict(s)
        return None

    @classmethod
    def create_salesperson(cls, name, phone='', email=''):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Salesperson name is required.")
        for s in cls._store.get('Salespersons', []):
            if s.get('Name', '').strip().lower() == clean_name.lower():
                raise ValueError(f"Salesperson '{clean_name}' already exists.")
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        new_id = f"SP-{len(cls._store.get('Salespersons', [])) + 1:03d}"
        item = {
            'Salesperson ID': new_id,
            'Name': clean_name,
            'Phone': phone.strip(),
            'Email': email.strip().lower(),
            'Is Active': 'TRUE',
            'Created At': now_str
        }
        cls._store.setdefault('Salespersons', []).append(item)
        return dict(item)

    @classmethod
    def update_salesperson(cls, sp_id, name, phone='', email='', is_active='TRUE'):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Salesperson name is required.")
        found = None
        old_name = None
        for s in cls._store.get('Salespersons', []):
            if s.get('Salesperson ID', '').strip() == sp_id.strip():
                found = s
                old_name = s.get('Name')
            elif s.get('Name', '').strip().lower() == clean_name.lower():
                raise ValueError(f"Another Salesperson named '{clean_name}' already exists.")
        if not found:
            raise ValueError(f"Salesperson '{sp_id}' not found.")
        found['Name'] = clean_name
        found['Phone'] = phone.strip()
        found['Email'] = email.strip().lower()
        found['Is Active'] = 'TRUE' if str(is_active).upper() in ('TRUE', '1', 'YES') else 'FALSE'
        if old_name and old_name != clean_name:
            cls._rename_salesperson_in_complaints(old_name, clean_name)
        return dict(found)

    @classmethod
    def get_salesperson_usage(cls, sp_id_or_name):
        sp = None
        for s in cls._store.get('Salespersons', []):
            if s.get('Salesperson ID', '').strip() == sp_id_or_name.strip() or \
               s.get('Name', '').strip().lower() == sp_id_or_name.strip().lower():
                sp = s
                break
        if not sp:
            raise ValueError(f"Salesperson '{sp_id_or_name}' not found.")
        target = sp['Name'].strip().lower()
        linked = [c.get('Complaint ID') for c in cls._store.get('Complaints', []) if (c.get('Salesperson Name') or '').strip().lower() == target]
        return sp, len(linked), linked

    @classmethod
    def delete_salesperson(cls, sp_id):
        sp, usage_count, linked_ids = cls.get_salesperson_usage(sp_id)
        if usage_count > 0:
            raise ValueError(
                f"Cannot delete Salesperson '{sp['Name']}': Referenced in {usage_count} "
                f"complaint(s) ({', '.join(linked_ids[:5])}{'...' if len(linked_ids) > 5 else ''}). "
                f"Please deactivate this salesperson instead to preserve historical records."
            )
        found_idx = None
        for idx, s in enumerate(cls._store.get('Salespersons', [])):
            if s.get('Salesperson ID', '').strip() == sp_id.strip():
                found_idx = idx
                break
        if found_idx is None:
            raise ValueError(f"Salesperson '{sp_id}' not found.")
        cls._store['Salespersons'].pop(found_idx)
        return True

    @classmethod
    def _rename_salesperson_in_complaints(cls, old_name, new_name):
        target_old = old_name.strip().lower()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for c in cls._store.get('Complaints', []):
            raw = (c.get('Salesperson Name') or '').strip().lower()
            if raw == target_old:
                c['Salesperson Name'] = new_name
                c['Last Updated At'] = now_str

    @classmethod
    def _normalize_complaint_record(cls, record):
        if not record:
            return record
        c = dict(record)
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

        if not c.get('From Department'):
            c['From Department'] = c.get('from_department') or 'None'
        if c.get('From Department') not in ('Online', 'Offline', 'Packing', 'None'):
            c['From Department'] = 'None'
        c['from_department'] = c['From Department']

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
    def lodge_complaint(cls, **kwargs):
        date_part = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')
        complaint_id = f"CMP-{date_part}-{uuid.uuid4().hex[:4].upper()}"
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')
        today_str = now_dt.strftime('%Y-%m-%d')

        complainant_email = (kwargs.get('complainant_email') or kwargs.get('email') or '').strip().lower()
        customer_name = (kwargs.get('customer_name') or kwargs.get('complainant_name') or kwargs.get('employee_name') or 'Anonymous').strip()
        employee_name = (kwargs.get('employee_name') or kwargs.get('customer_name') or customer_name).strip()
        complain_date = (kwargs.get('complain_date') or today_str).strip()
        invoice_no = (kwargs.get('invoice_no') or '').strip()
        complain_types = (kwargs.get('complain_types') or kwargs.get('subject') or 'General').strip()
        priority = (kwargs.get('priority') or 'Medium').capitalize()
        remarks = (kwargs.get('remarks') or '').strip()
        complain_details = (kwargs.get('complain_details') or kwargs.get('description') or remarks or 'No details provided').strip()
        customer_mobile_no = (kwargs.get('customer_mobile_no') or '').strip()
        department_name = (kwargs.get('department_name') or kwargs.get('department') or 'General').strip()
        category = (kwargs.get('category') or '').strip()
        vendor_name = (kwargs.get('vendor_name') or '').strip()
        salesperson_name = (kwargs.get('salesperson_name') or '').strip()
        status = (kwargs.get('status') or 'Submitted').strip()
        closing_date = (kwargs.get('closing_date') or '').strip()
        solution_given = (kwargs.get('solution_given') or '').strip()
        handled_by = (kwargs.get('handled_by') or kwargs.get('assigned_to_name') or 'Unassigned').strip()

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

        record = {
            'Complaint ID': complaint_id,
            'Timestamp': now_str,
            'Complainant Email': complainant_email,
            'Employee Name': sanitize_cell_value(employee_name),
            'Complain Date': complain_date,
            'Customer Name': sanitize_cell_value(customer_name),
            'Invoice No.': sanitize_cell_value(invoice_no),
            'Complain Types': sanitize_cell_value(complain_types),
            'Priority': priority,
            'Remarks': sanitize_cell_value(remarks),
            'Complain Details': sanitize_cell_value(complain_details),
            'Customer Mobile No.': sanitize_cell_value(customer_mobile_no),
            'Department Name': sanitize_cell_value(department_name),
            'Category': sanitize_cell_value(category),
            'Vendor Name': sanitize_cell_value(vendor_name),
            'Salesperson Name': sanitize_cell_value(salesperson_name),
            'Status': status,
            'Closing Date': closing_date,
            'Solution Given': sanitize_cell_value(solution_given),
            'Handled By': sanitize_cell_value(handled_by),
            'Total Time Taken': total_time_taken,
            'Comments': sanitize_cell_value(comments),
            'GR Amount': gr_amount,
            'GR Expected Date': gr_expected_date,
            'Attachment Link': attachment_link,
            'Last Updated At': now_str,
            'From Department': from_department
        }

        cls._store['Complaints'].append(record)

        cls.add_status_history(
            complaint_id=complaint_id,
            old_status='-',
            new_status=status,
            changed_by_name=customer_name,
            changed_by_email=complainant_email,
            remarks="Complaint lodged in system."
        )
        return cls._normalize_complaint_record(record)

    @classmethod
    def bulk_create_complaints(cls, complaints_list, current_user):
        """Mock bulk creation of complaint records."""
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        user_name = current_user.get('name') or current_user.get('Full Name') or 'Staff'
        user_email = current_user.get('email') or current_user.get('Email') or ''

        created_records = []
        for c in complaints_list:
            complaint_id = c.get('Complaint ID') or cls.generate_complaint_id()
            record = {
                'Complaint ID': complaint_id,
                'Timestamp': now_str,
                'Complainant Email': user_email,
                'Employee Name': user_name,
                'Complain Date': (c.get('Complain Date') or c.get('complain_date') or now_str.split(' ')[0]).strip(),
                'Customer Name': (c.get('Customer Name') or c.get('customer_name') or c.get('Complainant Name') or '').strip(),
                'Customer Mobile No.': (c.get('Customer Mobile No.') or c.get('customer_mobile') or '').strip(),
                'Invoice No.': (c.get('Invoice No.') or c.get('invoice_no') or '').strip(),
                'Complain Types': (c.get('Complain Types') or c.get('complain_types') or c.get('Subject') or '').strip(),
                'Department Name': (c.get('Department Name') or c.get('department_name') or c.get('Department') or 'General').strip(),
                'From Department': (c.get('From Department') or c.get('from_department') or 'None').strip(),
                'Priority': (c.get('Priority') or c.get('priority') or 'Medium').strip(),
                'Status': (c.get('Status') or c.get('status') or 'Submitted').strip(),
                'Category': (c.get('Category') or c.get('category') or '').strip(),
                'Vendor Name': (c.get('Vendor Name') or c.get('vendor_name') or '').strip(),
                'Salesperson Name': (c.get('Salesperson Name') or c.get('salesperson_name') or '').strip(),
                'Handled By': (c.get('Handled By') or c.get('handled_by') or 'Unassigned').strip(),
                'Remarks': (c.get('Remarks') or c.get('remarks') or '').strip(),
                'Complain Details': (c.get('Complain Details') or c.get('complain_details') or c.get('Description') or '').strip(),
                'Closing Date': (c.get('Closing Date') or c.get('closing_date') or '').strip(),
                'Solution Given': (c.get('Solution Given') or c.get('solution_given') or '').strip(),
                'GR Amount': str(c.get('GR Amount') or c.get('gr_amount') or '').strip(),
                'GR Expected Date': (c.get('GR Expected Date') or c.get('gr_expected_date') or '').strip(),
                'Attachment Link': (c.get('Attachment Link') or c.get('attachment_link') or '').strip(),
                'Comments': (c.get('Comments') or '').strip(),
                'Total Time Taken': (c.get('Total Time Taken') or '').strip(),
                'Last Updated At': now_str
            }
            if record['From Department'] not in ('Online', 'Offline', 'Packing', 'None'):
                record['From Department'] = 'None'

            cls._store['Complaints'].append(record)
            normalized = cls._normalize_complaint_record(record)
            created_records.append(normalized)

            cls._store['Status History'].append({
                'History ID': f"HST-{uuid.uuid4().hex[:6].upper()}",
                'Complaint ID': complaint_id,
                'Timestamp': now_str,
                'Old Status': '-',
                'New Status': record['Status'],
                'Changed By Name': user_name,
                'Changed By Email': user_email,
                'Remarks': "Bulk imported via Excel."
            })

        return created_records

    @classmethod
    def get_all_complaints(cls):
        """Returns all normalized complaint records."""
        return [cls._normalize_complaint_record(r) for r in cls._store['Complaints']]

    @classmethod
    def get_complaint_by_id(cls, complaint_id):
        if not complaint_id: return None
        for c in cls._store['Complaints']:
            if c.get('Complaint ID', '').strip() == complaint_id.strip():
                return cls._normalize_complaint_record(c)
        return None

    @classmethod
    def get_complaints_for_user(cls, user_email, role='User', user_departments=None, user_dict=None):
        normalized = [cls._normalize_complaint_record(c) for c in cls._store['Complaints']]
        user = dict(user_dict) if user_dict else {
            'email': user_email,
            'role': role,
            'department': user_departments or '',
            'departments': user_departments if isinstance(user_departments, list) else None
        }
        custom_roles = cls.get_all_roles()
        filtered = RBACService.filter_complaints_by_scope(normalized, user, custom_roles=custom_roles)
        return list(reversed(filtered))

    @classmethod
    def update_complaint(cls, complaint_id, data, changed_by_user):
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for c in cls._store['Complaints']:
            if c.get('Complaint ID', '').strip() == complaint_id.strip():
                old_status = c.get('Status', '')

                # Update fields
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
                    for hk in list(c.keys()):
                        if hk.lower() == k.lower() or hk.lower().replace(' ', '_') == k.lower():
                            if hk not in ('Complaint ID', 'Timestamp'):
                                c[hk] = sanitize_cell_value(str(v).strip())

                if 'From Department' in c and c['From Department']:
                    if c['From Department'] not in ('Online', 'Offline', 'Packing', 'None'):
                        c['From Department'] = 'None'

                if 'Priority' in c and c['Priority']:
                    c['Priority'] = c['Priority'].capitalize()

                # Auto calculate total time taken if empty
                complain_d = c.get('Complain Date', '')
                closing_d = c.get('Closing Date', '')
                if closing_d and complain_d and not clean_input.get('Total Time Taken') and not clean_input.get('total_time_taken'):
                    try:
                        d1 = datetime.datetime.strptime(complain_d, '%Y-%m-%d')
                        d2 = datetime.datetime.strptime(closing_d, '%Y-%m-%d')
                        diff_days = (d2 - d1).days
                        c['Total Time Taken'] = f"{max(0, diff_days)} day{'s' if diff_days != 1 else ''}"
                    except Exception:
                        pass

                c['Last Updated At'] = now_str
                new_status = c.get('Status', old_status)
                remarks = clean_input.get('remarks') or clean_input.get('Remarks') or f"Complaint updated by {changed_by_user.get('role', 'Staff')}."

                if old_status != new_status or clean_input.get('remarks'):
                    cls.add_status_history(
                        complaint_id=complaint_id,
                        old_status=old_status or 'Submitted',
                        new_status=new_status,
                        changed_by_name=changed_by_user.get('name') or changed_by_user.get('Full Name', 'Staff'),
                        changed_by_email=changed_by_user.get('email') or changed_by_user.get('Email', ''),
                        remarks=remarks
                    )

                return cls._normalize_complaint_record(c)
        raise ValueError(f"Complaint '{complaint_id}' not found.")

    @classmethod
    def delete_complaint(cls, complaint_id, deleted_by_user):
        found_idx = None
        old_status = 'Submitted'
        for idx, c in enumerate(cls._store['Complaints']):
            if c.get('Complaint ID', '').strip() == complaint_id.strip():
                found_idx = idx
                old_status = c.get('Status', 'Submitted')
                break

        if found_idx is None:
            raise ValueError(f"Complaint '{complaint_id}' not found.")

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
        cls._store['Complaints'].pop(found_idx)
        return True

    @classmethod
    def update_complaint_status(cls, complaint_id, new_status, changed_by_user, remarks=''):
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        today_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
        for c in cls._store['Complaints']:
            if c.get('Complaint ID', '').strip() == complaint_id.strip():
                old_status = c.get('Status', '')
                c['Status'] = new_status
                c['Last Updated At'] = now_str
                if new_status.lower() in ('resolved', 'closed'):
                    c['Closing Date'] = today_str
                    c['Resolved At'] = today_str
                    if remarks:
                        c['Solution Given'] = remarks
                        c['Resolution Summary'] = remarks
                cls.add_status_history(
                    complaint_id=complaint_id,
                    old_status=old_status or 'Under Review',
                    new_status=new_status,
                    changed_by_name=changed_by_user.get('name') or changed_by_user.get('Full Name', 'Staff'),
                    changed_by_email=changed_by_user.get('email') or changed_by_user.get('Email', ''),
                    remarks=remarks
                )
                return True
        raise ValueError(f"Complaint '{complaint_id}' not found.")

    @classmethod
    def assign_complaint(cls, complaint_id, assigned_to_name, assigned_to_email, assigned_by_user):
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for c in cls._store['Complaints']:
            if c.get('Complaint ID', '').strip() == complaint_id.strip():
                old_status = c.get('Status', '')
                c['Handled By'] = assigned_to_name
                c['Assigned To Name'] = assigned_to_name
                c['Assigned To Email'] = assigned_to_email
                c['Last Updated At'] = now_str
                if old_status.lower() == 'submitted':
                    c['Status'] = 'Under Review'
                    cls.add_status_history(
                        complaint_id=complaint_id,
                        old_status=old_status,
                        new_status='Under Review',
                        changed_by_name=assigned_by_user.get('name') or assigned_by_user.get('Full Name', 'Staff'),
                        changed_by_email=assigned_by_user.get('email') or assigned_by_user.get('Email', ''),
                        remarks=f"Assigned to {assigned_to_name}."
                    )
                cls.add_comment(
                    complaint_id=complaint_id,
                    author_name=assigned_by_user.get('name') or assigned_by_user.get('Full Name', 'Staff'),
                    author_email=assigned_by_user.get('email') or assigned_by_user.get('Email', ''),
                    author_role=assigned_by_user.get('role') or assigned_by_user.get('Role', 'Admin'),
                    comment_type='Internal Note',
                    message=f"Complaint assigned to {assigned_to_name} ({assigned_to_email})."
                )
                return True
        raise ValueError(f"Complaint '{complaint_id}' not found.")

    @classmethod
    def add_status_history(cls, complaint_id, old_status, new_status,
                           changed_by_name, changed_by_email, remarks=''):
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        cls._store['Status History'].append({
            'History ID': f"HST-{uuid.uuid4().hex[:6].upper()}",
            'Complaint ID': complaint_id,
            'Timestamp': now_str,
            'Old Status': old_status,
            'New Status': new_status,
            'Changed By Name': changed_by_name,
            'Changed By Email': changed_by_email,
            'Remarks': remarks
        })

    @classmethod
    def get_status_history(cls, complaint_id):
        return [dict(h) for h in cls._store['Status History'] if h.get('Complaint ID', '').strip() == complaint_id.strip()]

    @classmethod
    def add_comment(cls, complaint_id, author_name, author_email, author_role,
                    comment_type='Public Update', message=''):
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        record = {
            'Comment ID': f"CMT-{uuid.uuid4().hex[:6].upper()}",
            'Complaint ID': complaint_id,
            'Timestamp': now_str,
            'Author Name': author_name,
            'Author Email': author_email,
            'Author Role': author_role,
            'Comment Type': comment_type,
            'Message': message.strip()
        }
        cls._store['Assignments and Comments'].append(record)
        return dict(record)

    @classmethod
    def get_comments(cls, complaint_id, user_role='User', user_dict=None):
        all_c = [dict(c) for c in cls._store['Assignments and Comments'] if c.get('Complaint ID', '').strip() == complaint_id.strip()]
        can_view_internal = False
        canon_role = RBACService.canonical_role(user_dict.get('role') or user_dict.get('Role')) if user_dict else RBACService.canonical_role(user_role)
        if canon_role in ('Super Admin', 'Admin', 'Department Head', 'Department Staff'):
            can_view_internal = True
        elif user_dict:
            custom_roles = cls.get_all_roles()
            can_view_internal = RBACService.has_permission(user_dict, 'comments:add_internal', custom_roles=custom_roles)

        if not can_view_internal:
            return [c for c in all_c if c.get('Comment Type') != 'Internal Note']
        return all_c

    # =========================================================================
    # SYSTEM CONFIGURATION & OAUTH TOKEN PERSISTENCE (MOCK)
    # =========================================================================

    @classmethod
    def get_system_config(cls, key, default=None):
        for r in cls._store.get('System_Config', []):
            if str(r.get('Key', '')).strip().lower() == str(key).strip().lower():
                val = r.get('Value')
                if val is not None and val != '':
                    if is_sensitive_key(key):
                        return decrypt_secret(val)
                    return val
                return default
        return default

    @classmethod
    def get_all_system_config(cls, mask_sensitive=True):
        config_map = {}
        for r in cls._store.get('System_Config', []):
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

    @classmethod
    def set_system_config(cls, key, value, updated_by='system'):
        clean_key = str(key).strip()
        raw_val = '' if value is None else str(value).strip()

        if is_sensitive_key(clean_key) and raw_val:
            str_val = encrypt_secret(raw_val)
        else:
            str_val = raw_val

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        for r in cls._store['System_Config']:
            if str(r.get('Key', '')).strip().lower() == clean_key.lower():
                r['Value'] = str_val
                r['Updated_At'] = now_str
                r['Updated_By'] = updated_by
                return True

        cls._store['System_Config'].append({
            'Key': clean_key,
            'Value': str_val,
            'Updated_At': now_str,
            'Updated_By': updated_by
        })
        return True

    @classmethod
    def delete_system_config(cls, key):
        clean_key = str(key).strip().lower()
        initial_len = len(cls._store['System_Config'])
        cls._store['System_Config'] = [
            r for r in cls._store['System_Config']
            if str(r.get('Key', '')).strip().lower() != clean_key
        ]
        return len(cls._store['System_Config']) < initial_len

# Initialize default data on import
MockSheetsDB.reset()

# Convenience alias
