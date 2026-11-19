"""
AJIT ZONE PVT. LTD. - Complaint Management System
Enterprise Role-Based Access Control (RBAC) System

Provides:
- Granular permissions taxonomy and definitions
- System default role templates
- Department-level data scoping and record-level authorization
- Default-deny evaluation engine
- Privilege escalation prevention
- Audit log formatter
"""

import logging
import datetime
from typing import List, Dict, Set, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Data Scopes
DATA_SCOPE_ALL = 'ALL'
DATA_SCOPE_DEPARTMENT = 'DEPARTMENT'
DATA_SCOPE_OWN = 'OWN'
ALL_DATA_SCOPES = [DATA_SCOPE_ALL, DATA_SCOPE_DEPARTMENT, DATA_SCOPE_OWN]

# =============================================================================
# 1. GRANULAR PERMISSIONS TAXONOMY
# =============================================================================

ALL_PERMISSIONS = [
    # Complaints
    {
        'id': 'complaints:view',
        'name': 'View Complaints',
        'category': 'Complaints',
        'description': 'View complaint list, full records, and timeline within authorized department scope.'
    },
    {
        'id': 'complaints:create',
        'name': 'Create Complaint',
        'category': 'Complaints',
        'description': 'Lodge and submit new customer complaints.'
    },
    {
        'id': 'complaints:edit',
        'name': 'Edit Complaint',
        'category': 'Complaints',
        'description': 'Modify complaint details, metadata, priority, and resolution notes.'
    },
    {
        'id': 'complaints:delete',
        'name': 'Delete Complaint',
        'category': 'Complaints',
        'description': 'Permanently remove complaint records from database.'
    },
    {
        'id': 'complaints:assign',
        'name': 'Assign Complaint',
        'category': 'Complaints',
        'description': 'Assign or reassign complaints to support staff and department handlers.'
    },
    {
        'id': 'complaints:change_status',
        'name': 'Change Complaint Status',
        'category': 'Complaints',
        'description': 'Transition complaint workflow status (e.g. Under Review, In Progress, Resolved).'
    },
    {
        'id': 'complaints:close',
        'name': 'Close Complaint',
        'category': 'Complaints',
        'description': 'Mark complaint as Closed with resolution summary.'
    },
    {
        'id': 'complaints:reopen',
        'name': 'Reopen Complaint',
        'category': 'Complaints',
        'description': 'Reopen a previously resolved or closed complaint for further investigation.'
    },

    # Comments
    {
        'id': 'comments:view',
        'name': 'View Comments',
        'category': 'Comments',
        'description': 'View public updates and authorized discussion threads on complaints.'
    },
    {
        'id': 'comments:add',
        'name': 'Add Comment',
        'category': 'Comments',
        'description': 'Post public updates on complaints.'
    },
    {
        'id': 'comments:add_internal',
        'name': 'Add Internal Staff Note',
        'category': 'Comments',
        'description': 'Post staff-only confidential internal notes on complaints.'
    },
    {
        'id': 'comments:edit_own',
        'name': 'Edit Own Comments',
        'category': 'Comments',
        'description': 'Modify comments previously authored by the user.'
    },
    {
        'id': 'comments:delete_own',
        'name': 'Delete Own Comments',
        'category': 'Comments',
        'description': 'Delete comments previously authored by the user.'
    },

    # Attachments
    {
        'id': 'attachments:view',
        'name': 'View & Download Attachments',
        'category': 'Attachments',
        'description': 'View and download uploaded files on authorized complaints.'
    },
    {
        'id': 'attachments:upload',
        'name': 'Upload Attachments',
        'category': 'Attachments',
        'description': 'Upload files or attach document links to complaints.'
    },
    {
        'id': 'attachments:delete',
        'name': 'Delete Attachments',
        'category': 'Attachments',
        'description': 'Remove uploaded file attachments from complaints.'
    },

    # User Management
    {
        'id': 'users:view',
        'name': 'View Users',
        'category': 'Users',
        'description': 'View user directory, roles, and department assignments.'
    },
    {
        'id': 'users:create',
        'name': 'Create Users',
        'category': 'Users',
        'description': 'Create and provision new user accounts.'
    },
    {
        'id': 'users:edit',
        'name': 'Edit Users',
        'category': 'Users',
        'description': 'Update user names, emails, roles, and department memberships.'
    },
    {
        'id': 'users:deactivate',
        'name': 'Deactivate / Archive Users',
        'category': 'Users',
        'description': 'Deactivate or soft-delete user accounts while preserving history.'
    },
    {
        'id': 'users:reactivate',
        'name': 'Reactivate Users',
        'category': 'Users',
        'description': 'Re-enable previously deactivated user accounts.'
    },
    {
        'id': 'users:delete',
        'name': 'Permanently Delete Users',
        'category': 'Users',
        'description': 'Permanently delete user accounts after dependency checks.'
    },
    {
        'id': 'users:reset_password',
        'name': 'Reset User Passwords',
        'category': 'Users',
        'description': 'Trigger secure password resets for user accounts.'
    },
    {
        'id': 'users:assign_role',
        'name': 'Assign User Roles',
        'category': 'Users',
        'description': 'Change user roles and security privilege tiers.'
    },

    # Departments
    {
        'id': 'departments:view',
        'name': 'View Departments',
        'category': 'Departments',
        'description': 'View list of company departments and leads.'
    },
    {
        'id': 'departments:create',
        'name': 'Create Department',
        'category': 'Departments',
        'description': 'Add new company departments.'
    },
    {
        'id': 'departments:edit',
        'name': 'Edit Department',
        'category': 'Departments',
        'description': 'Modify department details, lead emails, and names.'
    },
    {
        'id': 'departments:deactivate',
        'name': 'Deactivate Department',
        'category': 'Departments',
        'description': 'Deactivate departments from new selections.'
    },
    {
        'id': 'departments:delete',
        'name': 'Delete Department',
        'category': 'Departments',
        'description': 'Delete empty or reassigned departments.'
    },

    # Master Data
    {
        'id': 'master_data:view',
        'name': 'View Master Data',
        'category': 'Master Data',
        'description': 'View Complaint Types and Salespersons master registries.'
    },
    {
        'id': 'master_data:manage',
        'name': 'Manage Master Data',
        'category': 'Master Data',
        'description': 'Create, edit, activate/deactivate, and delete Complaint Types and Salespersons.'
    },

    # Roles & Permissions
    {
        'id': 'roles:view',
        'name': 'View Roles & Permissions',
        'category': 'Roles & Permissions',
        'description': 'Inspect system roles, permission templates, and authorization matrix.'
    },
    {
        'id': 'roles:manage',
        'name': 'Manage Roles & Permissions',
        'category': 'Roles & Permissions',
        'description': 'Create custom roles, edit permission matrices, and configure authorization scopes.'
    },

    # Reports & Analytics
    {
        'id': 'reports:view',
        'name': 'View Reports & Analytics',
        'category': 'Reports & Analytics',
        'description': 'Access Reports Hub, analytics dashboards, and KPI metrics.'
    },
    {
        'id': 'reports:create',
        'name': 'Run Custom Report Queries',
        'category': 'Reports & Analytics',
        'description': 'Filter, group, and query complaint records dynamically.'
    },
    {
        'id': 'reports:save',
        'name': 'Save Report Presets',
        'category': 'Reports & Analytics',
        'description': 'Save custom column selections and report filter templates.'
    },
    {
        'id': 'reports:export_excel',
        'name': 'Export Excel Reports',
        'category': 'Reports & Analytics',
        'description': 'Export complaint reports in Microsoft Excel (.xlsx) format.'
    },
    {
        'id': 'reports:export_csv',
        'name': 'Export CSV Reports',
        'category': 'Reports & Analytics',
        'description': 'Export complaint reports in CSV (.csv) format.'
    },
    {
        'id': 'reports:export_pdf',
        'name': 'Export PDF Reports',
        'category': 'Reports & Analytics',
        'description': 'Export formatted executive reports in PDF (.pdf) format.'
    },
    {
        'id': 'reports:print',
        'name': 'Print Reports',
        'category': 'Reports & Analytics',
        'description': 'View and print clean tabular reports via browser print engine.'
    },

    # Audit Logs
    {
        'id': 'audit_logs:view',
        'name': 'View Audit Logs',
        'category': 'Audit Logs',
        'description': 'Inspect historical security logs, permission changes, and action records.'
    },
    {
        'id': 'audit_logs:export',
        'name': 'Export Audit Logs',
        'category': 'Audit Logs',
        'description': 'Export security audit trails for compliance.'
    },

    # System Settings
    {
        'id': 'settings:view',
        'name': 'View System Settings',
        'category': 'Settings',
        'description': 'View Google Sheets status, Google Drive OAuth connection, and network info.'
    },
    {
        'id': 'settings:modify',
        'name': 'Modify System Settings',
        'category': 'Settings',
        'description': 'Configure OAuth credentials, storage targets, and system parameters.'
    }
]

ALL_PERMISSION_IDS: Set[str] = {p['id'] for p in ALL_PERMISSIONS}
PERMISSION_MAP: Dict[str, Dict[str, Any]] = {p['id']: p for p in ALL_PERMISSIONS}


# =============================================================================
# 2. DEFAULT ROLE TEMPLATES
# =============================================================================

DEFAULT_ROLE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    'Super Admin': {
        'id': 'ROLE-001',
        'name': 'Super Admin',
        'description': 'Unrestricted system access across all departments, security settings, and configurations.',
        'data_scope': 'ALL',
        'permissions': list(ALL_PERMISSION_IDS),
        'is_system_role': True
    },
    'Admin': {
        'id': 'ROLE-002',
        'name': 'Admin',
        'description': 'Full administrative access to manage operations, users, departments, master data, reports, and complaints.',
        'data_scope': 'ALL',
        'permissions': [
            'complaints:view', 'complaints:create', 'complaints:edit', 'complaints:delete',
            'complaints:assign', 'complaints:change_status', 'complaints:close', 'complaints:reopen',
            'comments:view', 'comments:add', 'comments:add_internal', 'comments:edit_own', 'comments:delete_own',
            'attachments:view', 'attachments:upload', 'attachments:delete',
            'users:view', 'users:create', 'users:edit', 'users:deactivate', 'users:reactivate',
            'users:reset_password', 'users:assign_role',
            'departments:view', 'departments:create', 'departments:edit', 'departments:deactivate', 'departments:delete',
            'master_data:view', 'master_data:manage',
            'roles:view',
            'reports:view', 'reports:create', 'reports:save', 'reports:export_excel', 'reports:export_csv',
            'reports:export_pdf', 'reports:print',
            'audit_logs:view', 'audit_logs:export',
            'settings:view', 'settings:modify'
        ],
        'is_system_role': True
    },
    'Department Head': {
        'id': 'ROLE-003',
        'name': 'Department Head',
        'description': 'Operational leadership for assigned department(s). Full complaint workflow control, staff assignment, and department reports.',
        'data_scope': 'DEPARTMENT',
        'permissions': [
            'complaints:view', 'complaints:create', 'complaints:edit', 'complaints:assign',
            'complaints:change_status', 'complaints:close', 'complaints:reopen',
            'comments:view', 'comments:add', 'comments:add_internal', 'comments:edit_own', 'comments:delete_own',
            'attachments:view', 'attachments:upload',
            'users:view',
            'departments:view',
            'master_data:view',
            'reports:view', 'reports:create', 'reports:export_excel', 'reports:export_csv',
            'reports:export_pdf', 'reports:print'
        ],
        'is_system_role': True
    },
    'Department Staff': {
        'id': 'ROLE-004',
        'name': 'Department Staff',
        'description': 'Staff member handling and updating complaints assigned to their authorized department(s).',
        'data_scope': 'DEPARTMENT',
        'permissions': [
            'complaints:view', 'complaints:create', 'complaints:edit', 'complaints:change_status',
            'comments:view', 'comments:add', 'comments:add_internal', 'comments:edit_own',
            'attachments:view', 'attachments:upload',
            'departments:view',
            'master_data:view',
            'reports:view', 'reports:print'
        ],
        'is_system_role': True
    },
    'Viewer': {
        'id': 'ROLE-005',
        'name': 'Viewer',
        'description': 'Read-only access to authorized records, complaints, attachments, and reports.',
        'data_scope': 'DEPARTMENT',
        'permissions': [
            'complaints:view',
            'comments:view',
            'attachments:view',
            'departments:view',
            'reports:view', 'reports:print'
        ],
        'is_system_role': True
    },
    'User': {
        'id': 'ROLE-006',
        'name': 'User',
        'description': 'Standard complainant account. Lodges new complaints and tracks own submitted issues.',
        'data_scope': 'OWN',
        'permissions': [
            'complaints:view', 'complaints:create', 'complaints:edit',
            'comments:view', 'comments:add',
            'attachments:view', 'attachments:upload'
        ],
        'is_system_role': True
    }
}

# Role aliases for legacy compatibility
ROLE_ALIASES = {
    'Support Staff': 'Department Staff',
    'support staff': 'Department Staff',
    'Staff': 'Department Staff',
    'Customer': 'User',
    'SuperAdmin': 'Super Admin',
    'super admin': 'Super Admin',
    'admin': 'Admin'
}


# =============================================================================
# 3. RBAC SERVICE CLASS
# =============================================================================

class RBACService:
    """Centralized authorization and access control service."""

    @classmethod
    def canonical_role(cls, role_name: Optional[str]) -> str:
        """Resolves any legacy role alias to canonical role name."""
        if not role_name:
            return 'User'
        clean = role_name.strip()
        return ROLE_ALIASES.get(clean, clean)

    @classmethod
    def get_role_template(cls, role_name: str, custom_roles: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        """Finds role configuration from custom roles list or default templates."""
        canon = cls.canonical_role(role_name)

        # Check custom roles first if provided
        if custom_roles:
            for r in custom_roles:
                if (r.get('Name') or '').strip().lower() == canon.lower():
                    perms_raw = r.get('Permissions') or []
                    if isinstance(perms_raw, str):
                        perms_list = [p.strip() for p in perms_raw.split(',') if p.strip()]
                    else:
                        perms_list = list(perms_raw)
                    return {
                        'id': r.get('Role ID'),
                        'name': r.get('Name'),
                        'description': r.get('Description', ''),
                        'data_scope': r.get('Data Scope', 'DEPARTMENT').upper(),
                        'permissions': perms_list,
                        'is_system_role': str(r.get('Is System Role', 'FALSE')).upper() == 'TRUE'
                    }

        # Fallback to system default role templates
        if canon in DEFAULT_ROLE_TEMPLATES:
            return dict(DEFAULT_ROLE_TEMPLATES[canon])

        return None

    @classmethod
    def get_effective_permissions(cls, user: Dict[str, Any], custom_roles: Optional[List[Dict[str, Any]]] = None) -> Set[str]:
        """
        Calculates complete effective permissions for a user:
        Role permissions + user custom permissions (if any).
        Super Admin always possesses all permissions.
        """
        if not user:
            return set()

        # Inactive users possess zero permissions (strict default-deny)
        is_active = str(user.get('is_active') or user.get('Is Active') or 'TRUE').strip().upper()
        if is_active == 'FALSE':
            return set()

        role = cls.canonical_role(user.get('role') or user.get('Role'))
        if role == 'Super Admin':
            return set(ALL_PERMISSION_IDS) | {'*'}

        role_cfg = cls.get_role_template(role, custom_roles=custom_roles)
        permissions: Set[str] = set(role_cfg.get('permissions', [])) if role_cfg else set()

        # Handle individual custom user permission overrides if present
        custom_perms = user.get('custom_permissions') or user.get('Custom Permissions')
        if custom_perms:
            if isinstance(custom_perms, str):
                tokens = [p.strip() for p in custom_perms.split(',') if p.strip()]
            else:
                tokens = list(custom_perms)
            for tok in tokens:
                if tok.startswith('+'):
                    permissions.add(tok[1:])
                elif tok.startswith('-'):
                    permissions.discard(tok[1:])
                else:
                    permissions.add(tok)

        return permissions

    @classmethod
    def has_permission(cls, user: Dict[str, Any], permission_id: str, custom_roles: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Evaluates whether a user holds a specific granular permission.
        Enforces DEFAULT-DENY.
        """
        if not user:
            return False

        # Inactive or archived accounts have ZERO permissions
        is_active = str(user.get('is_active') or user.get('Is Active', 'TRUE')).upper()
        if is_active not in ('TRUE', '1', 'YES'):
            return False

        role = cls.canonical_role(user.get('role') or user.get('Role'))
        if role == 'Super Admin':
            return True

        # Fast path: check pre-computed permissions on user dict if present
        cached_perms = user.get('permissions') or user.get('Permissions')
        if cached_perms:
            if isinstance(cached_perms, str):
                tokens = {p.strip() for p in cached_perms.split(',') if p.strip()}
            else:
                tokens = set(cached_perms)
            if '*' in tokens or permission_id in tokens:
                return True

        effective = cls.get_effective_permissions(user, custom_roles=custom_roles)
        return permission_id in effective

    @classmethod
    def get_user_departments(cls, user: Dict[str, Any]) -> List[str]:
        """
        Parses user's authorized department list from list, comma/semicolon/pipe separated string.
        Returns unique list of clean trimmed department names.
        """
        if not user:
            return []

        raw = (
            user.get('departments')
            or user.get('Departments')
            or user.get('department')
            or user.get('Department')
            or ''
        )
        if isinstance(raw, list):
            items = raw
        else:
            items = str(raw).replace(';', ',').replace('|', ',').split(',')

        depts = []
        seen = set()
        for d in items:
            clean = str(d).strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                depts.append(clean)
        return depts

    @classmethod
    def parse_complaint_departments(cls, complaint: Dict[str, Any]) -> List[str]:
        """
        Parses all departments associated with a complaint.
        Includes both target 'Department Name' and source 'From Department' (Online, Offline, Packing, etc.).
        Supports comma, semicolon, pipe separated values.
        """
        if not complaint:
            return []

        dept_sources = []
        # Target Department Name (e.g. Kurti, Readymade, Saree)
        target_dept = (
            complaint.get('Department Name')
            or complaint.get('department_name')
            or complaint.get('Department')
            or complaint.get('department')
            or ''
        )
        if target_dept:
            dept_sources.append(target_dept)

        # Source / Origin Department (e.g. Online, Offline, Packing)
        from_dept = (
            complaint.get('From Department')
            or complaint.get('from_department')
            or complaint.get('FromDept')
            or ''
        )
        if from_dept and str(from_dept).strip().lower() not in ('none', 'n/a', ''):
            dept_sources.append(from_dept)

        depts = []
        seen = set()
        for src in dept_sources:
            if isinstance(src, list):
                items = src
            else:
                items = str(src).replace(';', ',').replace('|', ',').split(',')
            for item in items:
                clean = str(item).strip()
                if clean and clean.lower() not in seen:
                    seen.add(clean.lower())
                    depts.append(clean)
        return depts

    @classmethod
    def is_handled_by_user(cls, user: Dict[str, Any], complaint: Dict[str, Any]) -> bool:
        """
        Checks whether the given user is assigned as the handler ('Handled By') of the complaint.
        Handles:
        - Full name matches: 'Nita Chaudhary' == 'Nita Chaudhary'
        - First name / nickname / token matches: 'Nita' in 'Nita Chaudhary' or 'NITA' == 'Nita'
        - Email matches: 'ajitzonealleditor@gmail.com' == 'ajitzonealleditor@gmail.com'
        - Email username prefix matches: 'ajitzonealleditor'
        - Comma/slash separated multi-handler lists: 'Nita, Rahul'
        - Case and whitespace insensitive.
        """
        if not user or not complaint:
            return False

        # Check candidate fields: Handled By, Assigned To Name, Assigned To Email, Assigned To ID
        candidate_fields = [
            complaint.get('Handled By'),
            complaint.get('handled_by'),
            complaint.get('Assigned To Name'),
            complaint.get('assigned_to_name'),
            complaint.get('Assigned To Email'),
            complaint.get('assigned_to_email'),
            complaint.get('Handled By ID'),
            complaint.get('Assigned To ID')
        ]

        raw_strings = [str(f).strip() for f in candidate_fields if f and str(f).strip()]
        if not raw_strings:
            return False

        tokens = []
        ignored_placeholders = {'pending', 'unassigned', 'none', 'n/a', '-', ''}
        for rs in raw_strings:
            for tok in rs.replace('/', ',').split(','):
                clean_tok = tok.strip().lower()
                if clean_tok and clean_tok not in ignored_placeholders:
                    tokens.append(clean_tok)

        if not tokens:
            return False

        user_name = (user.get('name') or user.get('Full Name') or '').strip().lower()
        user_email = (user.get('email') or user.get('Email') or '').strip().lower()
        user_id = (user.get('user_id') or user.get('User ID') or '').strip().lower()

        name_parts = [p.strip().lower() for p in user_name.split() if p.strip()]
        email_prefix = user_email.split('@')[0].strip().lower() if '@' in user_email else ''

        for tok in tokens:
            # Exact match with full name, email, or user id
            if tok == user_name or tok == user_email or (user_id and tok == user_id):
                return True
            # Token matches first name or any component of user name (e.g. 'nita' matches 'nita chaudhary')
            if tok in name_parts:
                return True
            # Token matches email prefix
            if email_prefix and tok == email_prefix:
                return True
            # Prefix match: user name starts with token (length >= 3)
            if len(tok) >= 3 and user_name.startswith(tok):
                return True
            # Token starts with user name (e.g. 'nita chaudhary (sales)')
            if user_name and tok.startswith(user_name):
                return True

        return False

    @classmethod
    def is_created_by_user(cls, user: Dict[str, Any], complaint: Dict[str, Any]) -> bool:
        """
        Evaluates whether the given complaint was created/lodged by the user.
        Checks:
        - Complainant Email matches user email
        - Employee Name matches user name, email, or token
        - Customer Name matches user name (for regular users)
        - User ID / Created By matches user ID
        """
        if not user or not complaint:
            return False

        user_email = (user.get('email') or user.get('Email') or '').lower().strip()
        comp_owner = (complaint.get('Complainant Email') or complaint.get('email') or '').lower().strip()
        if user_email and comp_owner == user_email:
            return True

        user_name = (user.get('name') or user.get('Full Name') or '').lower().strip()
        emp_name = (complaint.get('Employee Name') or complaint.get('employee_name') or '').lower().strip()
        if user_name and emp_name:
            if user_name == emp_name or emp_name in user_name.split() or user_name.startswith(emp_name):
                return True
            if user_email and emp_name == user_email:
                return True

        user_id = str(user.get('user_id') or user.get('User ID') or '').lower().strip()
        created_by_id = str(complaint.get('User ID') or complaint.get('Created By') or complaint.get('user_id') or '').lower().strip()
        if user_id and created_by_id and user_id == created_by_id:
            return True

        return False

    @classmethod
    def is_my_complaint(cls, user: Dict[str, Any], complaint: Dict[str, Any]) -> bool:
        """
        Returns True if the complaint is assigned to OR created by the user.
        """
        return cls.is_created_by_user(user, complaint) or cls.is_handled_by_user(user, complaint)

    @classmethod
    def can_access_complaint(
        cls,
        user: Dict[str, Any],
        complaint: Dict[str, Any],
        action: str = 'view',
        custom_roles: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, str]:
        """
        Comprehensive record-level authorization check.
        Evaluates:
        1. Action permission (complaints:view, complaints:edit, complaints:delete, etc.)
        2. Data scope (ALL vs DEPARTMENT vs OWN)
        3. Multi-department alignment
        4. Ownership & Handled-By matching
        5. Workflow status constraints (e.g. regular users only edit while Submitted)

        Returns (allowed: bool, reason: str).
        """
        if not user:
            return False, "Authentication required."

        is_active = str(user.get('is_active') or user.get('Is Active', 'TRUE')).upper()
        if is_active not in ('TRUE', '1', 'YES'):
            return False, "Account is inactive or archived."

        role = cls.canonical_role(user.get('role') or user.get('Role'))

        # 1. Super Admin has unrestricted access to everything
        if role == 'Super Admin':
            return True, "Authorized as Super Admin."

        # 2. Check granular action permission
        perm_map = {
            'view': 'complaints:view',
            'edit': 'complaints:edit',
            'delete': 'complaints:delete',
            'assign': 'complaints:assign',
            'change_status': 'complaints:change_status',
            'close': 'complaints:close',
            'reopen': 'complaints:reopen'
        }
        required_perm = perm_map.get(action, 'complaints:view')
        if not cls.has_permission(user, required_perm, custom_roles=custom_roles):
            return False, f"Permission denied: Missing '{required_perm}'."

        # 3. Determine role data scope
        role_cfg = cls.get_role_template(role, custom_roles=custom_roles)
        data_scope = role_cfg.get('data_scope', 'DEPARTMENT') if role_cfg else 'DEPARTMENT'

        # If data scope is ALL (e.g. Admin), access is granted across all departments
        if data_scope == 'ALL':
            return True, "Authorized across all departments."

        is_owner = cls.is_created_by_user(user, complaint)

        # If user is the complaint creator, they can view their own record
        if action == 'view' and is_owner:
            return True, "Authorized as complaint owner."

        # Complainant / Creator Edit Matching
        if action == 'edit' and is_owner:
            if role == 'User':
                status = complaint.get('Status', 'Submitted')
                if status != 'Submitted':
                    return False, f"Cannot edit complaint in '{status}' status. Only 'Submitted' complaints can be edited by regular users."
                return True, "Authorized to edit submitted complaint."
            else:
                # Staff or Executive account who submitted the complaint is authorized to edit it
                return True, "Authorized to edit own complaint."

        # 4. Staff assigned as 'Handled By' can access (view, edit, change status, assign, etc.)
        is_assigned_staff = cls.is_handled_by_user(user, complaint)
        if is_assigned_staff:
            return True, "Authorized as assigned complaint handler."

        # 5. Department Scope matching
        user_depts = [d.lower() for d in cls.get_user_departments(user)]
        if '*' in user_depts or 'all' in user_depts:
            return True, "Authorized across all company departments."

        comp_depts = [d.lower() for d in cls.parse_complaint_departments(complaint)]

        # Check if ANY department in complaint matches ANY of user's authorized departments
        dept_match = any(cd in user_depts for cd in comp_depts) if user_depts and comp_depts else False
        if dept_match:
            return True, "Authorized by department scope."

        # If scope is OWN and not owner, deny
        if data_scope == 'OWN':
            return False, "Access denied: You can only access complaints lodged by your account."

        return False, "Access denied: Complaint does not belong to your authorized department(s)."

    @classmethod
    def filter_complaints_by_scope(
        cls,
        complaints: List[Dict[str, Any]],
        user: Dict[str, Any],
        custom_roles: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Filters a list of complaint records according to user's RBAC scope:
        - Super Admin & Admin (ALL scope): All records.
        - Department Head & Staff (DEPARTMENT scope): Records matching assigned departments, handled by them, or owned.
        - User (OWN scope): Only complaints matching complainant email.
        """
        if not user or not complaints:
            return []

        role = cls.canonical_role(user.get('role') or user.get('Role'))
        if role == 'Super Admin':
            return complaints

        role_cfg = cls.get_role_template(role, custom_roles=custom_roles)
        data_scope = role_cfg.get('data_scope', 'DEPARTMENT') if role_cfg else 'DEPARTMENT'
        if data_scope == 'ALL':
            return complaints

        user_email = (user.get('email') or user.get('Email') or '').lower().strip()
        user_depts = {d.lower() for d in cls.get_user_departments(user)}

        # If user is authorized for all departments
        if '*' in user_depts or 'all' in user_depts:
            return complaints

        filtered = []
        for c in complaints:
            if cls.is_created_by_user(user, c):
                filtered.append(c)
                continue

            if cls.is_handled_by_user(user, c):
                filtered.append(c)
                continue

            if data_scope == 'DEPARTMENT':
                comp_depts = {d.lower() for d in cls.parse_complaint_departments(c)}
                if user_depts.intersection(comp_depts):
                    filtered.append(c)
                    continue

        return filtered

    @classmethod
    def can_assign_role(cls, current_user: Dict[str, Any], target_role: str, custom_roles: Optional[List[Dict[str, Any]]] = None) -> Tuple[bool, str]:
        """
        Prevents privilege escalation:
        A user cannot assign a role with higher authority than their own.
        Only Super Admin can assign the Super Admin role.
        Returns (allowed: bool, reason: str).
        """
        if not current_user:
            return False, "Authentication required to assign roles."

        actor_role = cls.canonical_role(current_user.get('role') or current_user.get('Role'))
        target_canon = cls.canonical_role(target_role)

        if actor_role == 'Super Admin':
            return True, "Authorized as Super Admin."

        if target_canon == 'Super Admin':
            return False, "Access forbidden: Only Super Admins can assign the Super Admin role."

        if actor_role == 'Admin':
            return True, "Authorized as Admin."

        return False, f"Role '{actor_role}' does not have permission to assign roles."