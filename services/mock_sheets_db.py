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

DEFAULT_DEPARTMENTS = [
    ('IT & Technical Support', 'Hardware, software, network, email, and systems assistance.'),
    ('Facilities & Infrastructure', 'Building maintenance, electrical, plumbing, hygiene, and utilities.'),
    ('Finance & Billing', 'Salary, invoice processing, reimbursement, and billing inquiries.'),
    ('Human Resources', 'Workplace culture, policy guidance, onboarding, and employee relations.'),
    ('Customer Operations', 'Customer-facing services, service delivery, and logistics complaints.')
]

class MockSheetsDB:
    """
    Isolated In-Memory mock storage implementing the exact same database API as SheetsDB.
    Strictly used for offline unit testing, test suites, and mock verification.
    """
    _store = {
        'Complaints': [],
        'Users': [],
        'Departments': [],
        'Status History': [],
        'Assignments and Comments': [],
        'System_Config': []
    }

    @classmethod
    def reset(cls):
        """Resets in-memory store to clean initial state with bootstrap admin and departments."""
        cls._store = {
            'Complaints': [],
            'Users': [],
            'Departments': [],
            'Status History': [],
            'Assignments and Comments': [],
            'System_Config': []
        }
        # Bootstrap default admin if configured in environment
        if Config.INITIAL_ADMIN_PASSWORD:
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            cls._store['Users'].append({
                'User ID': 'USR-0001',
                'Full Name': Config.INITIAL_ADMIN_NAME,
                'Email': Config.INITIAL_ADMIN_EMAIL,
                'Password Hash': generate_password_hash(Config.INITIAL_ADMIN_PASSWORD),
                'Role': 'Super Admin',
                'Department': 'Management',
                'Is Active': 'TRUE',
                'Created At': now_str,
                'Last Login': ''
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
    def create_user(cls, full_name, email, password, role='User', department='General'):
        clean_email = email.lower().strip()
        if cls.find_user_by_email(clean_email):
            raise ValueError(f"User with email '{clean_email}' already exists.")
        user_num = len(cls._store['Users']) + 1
        user_id = f"USR-{user_num:04d}"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        record = {
            'User ID': user_id,
            'Full Name': full_name.strip(),
            'Email': clean_email,
            'Password Hash': generate_password_hash(password),
            'Role': role,
            'Department': department.strip(),
            'Is Active': 'TRUE',
            'Created At': now_str,
            'Last Login': ''
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
            'GR Expected Date'
        ]
        for f in canonical_fields:
            if f not in c:
                c[f] = ''

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
            'Last Updated At': now_str
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
    def get_complaint_by_id(cls, complaint_id):
        if not complaint_id: return None
        for c in cls._store['Complaints']:
            if c.get('Complaint ID', '').strip() == complaint_id.strip():
                return cls._normalize_complaint_record(c)
        return None

    @classmethod
    def get_complaints_for_user(cls, user_email, role='User'):
        normalized = [cls._normalize_complaint_record(c) for c in cls._store['Complaints']]
        if role in ('Super Admin', 'Admin', 'Support Staff'):
            return list(reversed(normalized))
        clean_email = user_email.lower().strip()
        filtered = [c for c in normalized if c.get('Complainant Email', '').lower().strip() == clean_email]
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
                    'assigned_to_name': 'Handled By'
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
    def get_comments(cls, complaint_id, user_role='User'):
        all_c = [dict(c) for c in cls._store['Assignments and Comments'] if c.get('Complaint ID', '').strip() == complaint_id.strip()]
        if user_role not in ('Super Admin', 'Admin', 'Support Staff'):
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
