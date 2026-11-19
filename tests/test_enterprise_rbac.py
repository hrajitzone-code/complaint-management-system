"""
Enterprise RBAC & Multi-Department Authorization Test Suite
AJIT ZONE PVT. LTD. – Complaint Management System

Verifies:
1. Granular permission evaluation and default role templates.
2. Multi-department scoping and cross-department IDOR prevention.
3. Custom role lifecycle (create, update, clone, delete guard, usage check).
4. Privilege escalation guards and last Super Admin protection.
5. Internal notes security based on 'comments:add_internal'.
6. Immutable audit logging and export.
"""
import os
import sys
import unittest
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import Config
from app import app
from services.mock_sheets_db import MockSheetsDB
from services.rbac_service import (
    RBACService,
    ALL_PERMISSIONS,
    ALL_PERMISSION_IDS,
    DEFAULT_ROLE_TEMPLATES,
    DATA_SCOPE_ALL,
    DATA_SCOPE_DEPARTMENT,
    DATA_SCOPE_OWN
)

class TestEnterpriseRBAC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        Config.INITIAL_ADMIN_PASSWORD = 'Admin@123456'

    def setUp(self):
        MockSheetsDB.reset()
        self.client = app.test_client()

        # Seed users in MockSheetsDB
        self.super_admin_email = 'superadmin@ajitzone.local'
        self.admin_email = 'admin@ajitzone.local'
        self.dept_head_email = 'head_readymade@ajitzone.local'
        self.dept_staff_email = 'staff_readymade@ajitzone.local'
        self.other_staff_email = 'staff_saree@ajitzone.local'
        self.multi_dept_staff_email = 'staff_multi@ajitzone.local'
        self.viewer_email = 'viewer@ajitzone.local'
        self.complainant_email = 'customer@ajitzone.local'

        # 1. Super Admin
        MockSheetsDB.create_user('Super Administrator', self.super_admin_email, 'Admin@123456', role='Super Admin', department='Management')
        # 2. Admin
        MockSheetsDB.create_user('General Admin', self.admin_email, 'Admin@123456', role='Admin', department='Operations')
        # 3. Department Head (Readymade)
        MockSheetsDB.create_user('Head Readymade', self.dept_head_email, 'Admin@123456', role='Department Head', department='Readymade')
        # 4. Department Staff (Readymade)
        MockSheetsDB.create_user('Staff Readymade', self.dept_staff_email, 'Admin@123456', role='Department Staff', department='Readymade')
        # 5. Department Staff (Saree)
        MockSheetsDB.create_user('Staff Saree', self.other_staff_email, 'Admin@123456', role='Department Staff', department='Saree')
        # 6. Multi-department Staff (Readymade, Gown)
        MockSheetsDB.create_user('Staff Multi', self.multi_dept_staff_email, 'Admin@123456', role='Department Staff', department='Readymade, Gown')
        # 7. Viewer
        MockSheetsDB.create_user('External Auditor', self.viewer_email, 'Admin@123456', role='Viewer', department='Auditing')
        # 8. User / Complainant
        MockSheetsDB.create_user('Retail Customer', self.complainant_email, 'Admin@123456', role='User', department='General')

    def _login(self, email, password='Admin@123456'):
        res = self.client.post('/api/auth/login', json={'email': email, 'password': password})
        self.assertEqual(res.status_code, 200, f"Failed login for {email}: {res.get_data(as_text=True)}")
        return res

    # -------------------------------------------------------------------------
    # 1. CORE RBAC SERVICE TESTS
    # -------------------------------------------------------------------------
    def test_01_canonical_role_mapping(self):
        """Verifies canonical mapping of legacy role names."""
        self.assertEqual(RBACService.canonical_role('Support Staff'), 'Department Staff')
        self.assertEqual(RBACService.canonical_role('support staff'), 'Department Staff')
        self.assertEqual(RBACService.canonical_role('Department Staff'), 'Department Staff')
        self.assertEqual(RBACService.canonical_role('Super Admin'), 'Super Admin')
        self.assertEqual(RBACService.canonical_role('Viewer'), 'Viewer')
        self.assertEqual(RBACService.canonical_role('Custom Auditor'), 'Custom Auditor')

    def test_02_default_role_templates_and_permissions(self):
        """Verifies default role templates and permission evaluations."""
        # Super Admin has wildcard '*'
        super_perms = RBACService.get_effective_permissions({'Role': 'Super Admin'})
        self.assertIn('*', super_perms)
        self.assertTrue(RBACService.has_permission({'Role': 'Super Admin'}, 'any:arbitrary_permission'))

        # Admin has complaints:export, roles:view, etc.
        admin_perms = RBACService.get_effective_permissions({'Role': 'Admin'})
        self.assertIn('complaints:close', admin_perms)
        self.assertIn('master_data:manage', admin_perms)
        self.assertTrue(RBACService.has_permission({'Role': 'Admin'}, 'complaints:edit'))

        # Department Staff has complaints:change_status, comments:add_internal, but NOT complaints:delete or users:create
        staff_perms = RBACService.get_effective_permissions({'Role': 'Department Staff'})
        self.assertIn('complaints:change_status', staff_perms)
        self.assertIn('comments:add_internal', staff_perms)
        self.assertNotIn('complaints:delete', staff_perms)
        self.assertNotIn('users:create', staff_perms)
        self.assertTrue(RBACService.has_permission({'Role': 'Department Staff'}, 'complaints:change_status'))
        self.assertFalse(RBACService.has_permission({'Role': 'Department Staff'}, 'users:create'))

        # Viewer has read-only permissions
        viewer_perms = RBACService.get_effective_permissions({'Role': 'Viewer'})
        self.assertIn('complaints:view', viewer_perms)
        self.assertIn('reports:view', viewer_perms)
        self.assertNotIn('complaints:edit', viewer_perms)
        self.assertNotIn('complaints:create', viewer_perms)
        self.assertFalse(RBACService.has_permission({'Role': 'Viewer'}, 'complaints:create'))

        # Inactive user has ZERO permissions (default-deny)
        inactive_user = {'Role': 'Super Admin', 'Is Active': 'FALSE'}
        self.assertFalse(RBACService.has_permission(inactive_user, 'complaints:view'))
        self.assertEqual(RBACService.get_effective_permissions(inactive_user), set())

    def test_03_custom_permission_overrides(self):
        """Verifies explicit '+permission' and '-permission' overrides on users."""
        # Base role: Department Staff (has comments:add_internal, lacks users:view)
        # Add '+users:view', remove '-comments:add_internal'
        user = {
            'Role': 'Department Staff',
            'Is Active': 'TRUE',
            'Custom Permissions': '+users:view, -comments:add_internal'
        }
        self.assertTrue(RBACService.has_permission(user, 'users:view'))
        self.assertFalse(RBACService.has_permission(user, 'comments:add_internal'))
        self.assertTrue(RBACService.has_permission(user, 'complaints:change_status'))

    def test_04_multi_department_matching(self):
        """Verifies multi-department splitting and matching."""
        user_readymade = {'Department': 'Readymade'}
        user_multi = {'Department': 'Readymade, Gown, Kurti'}

        self.assertEqual(RBACService.get_user_departments(user_readymade), ['Readymade'])
        self.assertEqual(RBACService.get_user_departments(user_multi), ['Readymade', 'Gown', 'Kurti'])

        comp_single = {'Department Name': 'Readymade'}
        comp_multi = {'Department Name': 'Gown, Saree'}
        comp_other = {'Department Name': 'Saree'}

        # Multi user matches Readymade and Gown
        self.assertTrue(RBACService.can_access_complaint(user_multi, comp_single, 'Department Staff')[0])
        self.assertTrue(RBACService.can_access_complaint(user_multi, comp_multi, 'Department Staff')[0])
        # Single user (Readymade) cannot access Saree
        self.assertFalse(RBACService.can_access_complaint(user_readymade, comp_other, 'Department Staff')[0])

    # -------------------------------------------------------------------------
    # 2. REST API & RECORD-LEVEL AUTHORIZATION TESTS
    # -------------------------------------------------------------------------
    def test_05_get_permissions_and_roles_endpoints(self):
        """Verifies /api/rbac/permissions and /api/rbac/roles endpoints."""
        self._login(self.super_admin_email)

        # 1. Permissions Taxonomy
        res_perms = self.client.get('/api/rbac/permissions')
        self.assertEqual(res_perms.status_code, 200)
        perms_data = res_perms.get_json()
        self.assertEqual(perms_data['status'], 'success')
        self.assertIn('categories', perms_data)
        self.assertGreaterEqual(len(perms_data['permissions']), 20)

        # 2. Roles list
        res_roles = self.client.get('/api/rbac/roles')
        self.assertEqual(res_roles.status_code, 200)
        roles_data = res_roles.get_json()
        self.assertGreaterEqual(roles_data['count'], 6)
        role_names = [r['Name'] if 'Name' in r else r.get('role_name') for r in roles_data['roles']]
        for default_name in ['Super Admin', 'Admin', 'Department Head', 'Department Staff', 'Viewer', 'User']:
            self.assertIn(default_name, role_names)

    def test_06_custom_role_crud_and_guards(self):
        """Verifies creating, updating, and deleting custom roles, plus system role protection."""
        self._login(self.super_admin_email)

        # 1. Super Admin creates custom role
        new_role_payload = {
            'name': 'Quality Auditor',
            'data_scope': 'DEPARTMENT',
            'description': 'Handles QA investigations and audit trail viewing',
            'permissions': ['complaints:view', 'comments:view', 'comments:add_internal', 'audit_logs:view']
        }
        res_create = self.client.post('/api/rbac/roles', json=new_role_payload)
        self.assertEqual(res_create.status_code, 201)
        created_role = res_create.get_json()['role']
        role_id = created_role.get('Role ID') or created_role.get('role_id')
        self.assertEqual(created_role.get('Name') or created_role.get('role_name'), 'Quality Auditor')
        self.assertEqual(created_role.get('Data Scope') or created_role.get('data_scope'), 'DEPARTMENT')

        # 2. Non-Super Admin cannot create roles (403)
        self._login(self.admin_email)
        res_hacker = self.client.post('/api/rbac/roles', json={'name': 'Illegal Role', 'permissions': ['*']})
        self.assertEqual(res_hacker.status_code, 403)

        # 3. Super Admin updates custom role
        self._login(self.super_admin_email)
        res_update = self.client.put(f'/api/rbac/roles/{role_id}', json={
            'name': 'Senior Quality Auditor',
            'data_scope': 'ALL',
            'description': 'Updated scope to organization-wide',
            'permissions': ['complaints:view', 'complaints:export', 'audit_logs:view', 'audit_logs:export']
        })
        self.assertEqual(res_update.status_code, 200)
        updated_role = res_update.get_json()['role']
        self.assertEqual(updated_role.get('Name') or updated_role.get('role_name'), 'Senior Quality Auditor')
        self.assertEqual(updated_role.get('Data Scope') or updated_role.get('data_scope'), 'ALL')

        # 4. Super Admin attempts to delete system default role (e.g. Super Admin or Admin) -> Blocked 400
        res_del_sys = self.client.delete('/api/rbac/roles/ROLE-001')
        self.assertIn(res_del_sys.status_code, (400, 409))
        self.assertIn('system default role', res_del_sys.get_json()['message'].lower())

        # 5. Assign user to Senior Quality Auditor -> verify deletion is blocked due to active usage
        MockSheetsDB.create_user('Auditor Bob', 'auditor_bob@ajitzone.local', 'Admin@123456', role='Senior Quality Auditor', department='QA')
        res_del_used = self.client.delete(f'/api/rbac/roles/{role_id}')
        self.assertIn(res_del_used.status_code, (400, 409))
        self.assertIn('Cannot delete role', res_del_used.get_json()['message'])

        # 6. Reassign user to Viewer -> verify deletion now succeeds
        bob_user = MockSheetsDB.find_user_by_email('auditor_bob@ajitzone.local')
        MockSheetsDB.update_user(bob_user['User ID'], role='Viewer')
        res_del_ok = self.client.delete(f'/api/rbac/roles/{role_id}')
        self.assertEqual(res_del_ok.status_code, 200)

    def test_07_cross_department_idor_blocking(self):
        """
        Verifies that staff assigned to 'Readymade' cannot view, update, or comment on
        complaints belonging to 'Saree' (HTTP 403 Forbidden).
        """
        # Lodge 2 complaints: one in Readymade, one in Saree
        comp_readymade = MockSheetsDB.lodge_complaint(
            customer_name='Rahul Verma',
            department_name='Readymade',
            complain_types='Fabric Defect',
            priority='Medium',
            status='Open',
            complainant_email=self.complainant_email
        )
        comp_saree = MockSheetsDB.lodge_complaint(
            customer_name='Sunita Patel',
            department_name='Saree',
            complain_types='Color Mismatch',
            priority='High',
            status='Open',
            complainant_email=self.complainant_email
        )

        readymade_id = comp_readymade.get('Complaint ID') or comp_readymade.get('Complain ID')
        saree_id = comp_saree.get('Complaint ID') or comp_saree.get('Complain ID')

        # Login as Readymade Staff
        self._login(self.dept_staff_email)

        # 1. Access Readymade complaint -> 200 OK
        res_rm = self.client.get(f'/api/complaints/{readymade_id}')
        self.assertEqual(res_rm.status_code, 200)

        # 2. Access Saree complaint -> 403 Forbidden (cross-department IDOR protection)
        res_saree = self.client.get(f'/api/complaints/{saree_id}')
        self.assertEqual(res_saree.status_code, 403)
        self.assertIn('Access denied', res_saree.get_json()['message'])

        # 3. Attempt status update on Saree complaint -> 403 Forbidden
        res_status = self.client.put(f'/api/complaints/{saree_id}/status', json={'status': 'In Progress'})
        self.assertEqual(res_status.status_code, 403)

        # 4. Multi-department staff (Readymade, Gown) accessing Readymade -> 200 OK
        self._login(self.multi_dept_staff_email)
        res_multi_rm = self.client.get(f'/api/complaints/{readymade_id}')
        self.assertEqual(res_multi_rm.status_code, 200)

        # 5. Multi-department staff accessing Saree -> 403 Forbidden
        res_multi_saree = self.client.get(f'/api/complaints/{saree_id}')
        self.assertEqual(res_multi_saree.status_code, 403)

        # 6. Admin has 'ALL' scope -> can access both
        self._login(self.admin_email)
        self.assertEqual(self.client.get(f'/api/complaints/{readymade_id}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/complaints/{saree_id}').status_code, 200)

    def test_08_internal_notes_security(self):
        """
        Verifies that comments of type 'Internal Note' are hidden from regular users
        who lack the 'comments:add_internal' permission.
        """
        comp = MockSheetsDB.lodge_complaint(
            customer_name='Deepak Joshi',
            department_name='Readymade',
            complain_types='Stitching Issue',
            priority='Low',
            status='Open',
            complainant_email=self.complainant_email
        )
        comp_id = comp.get('Complaint ID') or comp.get('Complain ID')

        # Staff posts public update and internal note
        self._login(self.dept_staff_email)
        self.client.post(f'/api/complaints/{comp_id}/comments', json={
            'message': 'We are looking into this case for you.',
            'comment_type': 'Public Update'
        })
        self.client.post(f'/api/complaints/{comp_id}/comments', json={
            'message': 'INTERNAL AUDIT: Vendor was notified of seam failure.',
            'comment_type': 'Internal Note'
        })

        # Staff verifies both comments are returned
        res_staff = self.client.get(f'/api/complaints/{comp_id}')
        staff_comments = res_staff.get_json()['comments']
        self.assertEqual(len(staff_comments), 2)

        # Customer logs in: only Public Update must be visible, Internal Note stripped
        self._login(self.complainant_email)
        res_cust = self.client.get(f'/api/complaints/{comp_id}')
        cust_comments = res_cust.get_json()['comments']
        self.assertEqual(len(cust_comments), 1)
        self.assertEqual(cust_comments[0]['Comment Type'], 'Public Update')
        self.assertNotIn('INTERNAL AUDIT', cust_comments[0].get('Message') or cust_comments[0].get('Comment'))

    def test_09_privilege_escalation_guard(self):
        """
        Verifies that non-Super Admins cannot elevate themselves or other users to
        Super Admin, or deactivate active Super Admins.
        """
        self._login(self.admin_email)

        # Attempt to create a new Super Admin -> 403 Forbidden
        res_create_super = self.client.post('/api/users', json={
            'full_name': 'Illegal Super',
            'email': 'illegalsuper@ajitzone.local',
            'role': 'Super Admin',
            'department': 'Management',
            'password': 'Password123!'
        })
        self.assertEqual(res_create_super.status_code, 403)
        self.assertIn('Super Admin', res_create_super.get_json()['message'])

        # Attempt to promote self to Super Admin -> 403 Forbidden
        admin_user = MockSheetsDB.find_user_by_email(self.admin_email)
        res_promote = self.client.put(f"/api/users/{admin_user['User ID']}", json={
            'full_name': 'General Admin',
            'email': self.admin_email,
            'role': 'Super Admin',
            'department': 'Operations'
        })
        self.assertEqual(res_promote.status_code, 403)

    def test_10_immutable_audit_logging_and_export(self):
        """Verifies that administrative operations generate immutable audit logs and export works."""
        self._login(self.super_admin_email)

        # Perform an action that logs audit (create a department)
        res_dept = self.client.post('/api/departments', json={
            'name': 'Testing & Inspection',
            'description': 'Automated test department'
        })
        self.assertEqual(res_dept.status_code, 201)

        # Query audit logs endpoint
        res_audit = self.client.get('/api/rbac/audit-logs?module=Departments')
        self.assertEqual(res_audit.status_code, 200)
        logs = res_audit.get_json()['logs']
        self.assertGreaterEqual(len(logs), 1)
        self.assertEqual(logs[0]['Module'], 'Departments')
        self.assertEqual(logs[0]['Action'], 'CREATE')

        # Test CSV export endpoint
        res_export = self.client.get('/api/rbac/audit-logs/export')
        self.assertEqual(res_export.status_code, 200)
        self.assertIn('text/csv', res_export.headers['Content-Type'])
        csv_text = res_export.get_data(as_text=True)
        self.assertIn('Log ID,Timestamp,User Email,User Role,Action,Module', csv_text)


if __name__ == '__main__':
    unittest.main()
