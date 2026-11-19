"""
Automated Test Suite for RBAC Role Builder
Ajit Zone Pvt. Ltd. - Complaint Management System

Verifies:
1. Role listing normalization (camelCase, TitleCase, parsed permissions list, boolean flags, live user counts).
2. Role detail retrieval.
3. Role usage endpoint (safe assigned users list, correct count, system role protection flag).
4. Custom role creation with permission validation and audit logging.
5. Custom role update with permission validation and system role rename protection.
6. Role deletion safety guards (cannot delete system roles, cannot delete roles with active assigned users, can delete unassigned custom roles).
7. Role builder RBAC enforcement (only users with 'roles:manage' can create/edit/delete).
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
    DEFAULT_ROLE_TEMPLATES,
    ALL_PERMISSION_IDS
)

class TestRoleBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        Config.INITIAL_ADMIN_PASSWORD = 'Admin@123456'

    def setUp(self):
        MockSheetsDB.reset()
        self.client = app.test_client()

        # Seed test accounts
        self.super_admin_email = 'superadmin@ajitzone.local'
        self.admin_email = 'admin@ajitzone.local'
        self.viewer_email = 'viewer@ajitzone.local'

        MockSheetsDB.create_user(
            full_name='Test Super Admin',
            email=self.super_admin_email,
            password='Admin@123456',
            role='Super Admin',
            department='Management'
        )

        MockSheetsDB.create_user(
            full_name='Test Admin',
            email=self.admin_email,
            password='Admin@123456',
            role='Admin',
            department='Operations'
        )

        MockSheetsDB.create_user(
            full_name='Test Viewer',
            email=self.viewer_email,
            password='Admin@123456',
            role='Viewer',
            department='Finance'
        )

    def _login(self, email, password='Admin@123456'):
        res = self.client.post('/api/auth/login', json={'email': email, 'password': password})
        self.assertEqual(res.status_code, 200, f"Failed to log in as {email}: {res.get_json()}")
        return res

    def test_01_list_roles_normalization_and_user_counts(self):
        """Verify GET /api/rbac/roles returns properly normalized roles with correct live counts."""
        self._login(self.super_admin_email)
        res = self.client.get('/api/rbac/roles')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')
        roles = data.get('roles', [])
        self.assertGreaterEqual(len(roles), 6)

        # Inspect Super Admin role (bootstrapped admin + seeded super admin = 2)
        super_role = next((r for r in roles if r.get('role_name') == 'Super Admin'), None)
        self.assertIsNotNone(super_role, "Super Admin role missing from roles list")
        self.assertEqual(super_role.get('role_id'), 'ROLE-001')
        self.assertEqual(super_role.get('Role ID'), 'ROLE-001')
        self.assertEqual(super_role.get('Name'), 'Super Admin')
        self.assertEqual(super_role.get('data_scope'), 'ALL')
        self.assertTrue(super_role.get('is_system'))
        self.assertIsInstance(super_role.get('permissions'), list)
        self.assertIn('*', super_role.get('permissions'))
        self.assertGreaterEqual(super_role.get('assigned_users_count'), 1)
        self.assertGreaterEqual(super_role.get('assigned_user_count'), 1)

        # Inspect Admin role (1 user seeded)
        admin_role = next((r for r in roles if r.get('role_name') == 'Admin'), None)
        self.assertIsNotNone(admin_role)
        self.assertEqual(admin_role.get('assigned_users_count'), 1)

        # Inspect Viewer role (1 user seeded)
        viewer_role = next((r for r in roles if r.get('role_name') == 'Viewer'), None)
        self.assertIsNotNone(viewer_role)
        self.assertEqual(viewer_role.get('assigned_users_count'), 1)

        # Inspect Department Staff role (0 users seeded)
        staff_role = next((r for r in roles if r.get('role_name') == 'Department Staff'), None)
        self.assertIsNotNone(staff_role)
        self.assertEqual(staff_role.get('assigned_users_count'), 0)

    def test_02_get_role_details(self):
        """Verify GET /api/rbac/roles/<role_id> returns normalized details."""
        self._login(self.super_admin_email)
        res = self.client.get('/api/rbac/roles/ROLE-001')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')
        role = data.get('role')
        self.assertEqual(role.get('role_id'), 'ROLE-001')
        self.assertEqual(role.get('role_name'), 'Super Admin')
        self.assertEqual(role.get('data_scope'), 'ALL')

    def test_03_role_usage_endpoint(self):
        """Verify GET /api/rbac/roles/<role_id>/usage returns correct counts and delete eligibility."""
        self._login(self.super_admin_email)

        # System role with users (Super Admin): can_delete must be False
        res = self.client.get('/api/rbac/roles/ROLE-001/usage')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertGreaterEqual(data.get('assigned_users_count'), 1)
        self.assertGreaterEqual(data.get('assigned_user_count'), 1)
        self.assertTrue(data.get('is_system_role'))
        self.assertFalse(data.get('can_delete'))
        self.assertGreaterEqual(len(data.get('assigned_users', [])), 1)

    def test_04_create_custom_role_flow(self):
        """Verify custom role creation, persistence, and audit logging."""
        self._login(self.super_admin_email)

        payload = {
            'role_name': 'Quality Inspector',
            'description': 'Inspects completed complaints before resolution',
            'data_scope': 'DEPARTMENT',
            'permissions': ['complaints:view', 'complaints:edit', 'comments:view', 'comments:add']
        }
        res = self.client.post('/api/rbac/roles', json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')
        created_role = data.get('role')
        self.assertEqual(created_role.get('role_name'), 'Quality Inspector')
        self.assertEqual(created_role.get('data_scope'), 'DEPARTMENT')
        self.assertFalse(created_role.get('is_system'))
        self.assertEqual(created_role.get('assigned_users_count'), 0)
        new_role_id = created_role.get('role_id')

        # Verify role now appears in GET /api/rbac/roles
        res2 = self.client.get('/api/rbac/roles')
        roles = res2.get_json().get('roles', [])
        found = next((r for r in roles if r.get('role_id') == new_role_id), None)
        self.assertIsNotNone(found)
        self.assertEqual(found.get('role_name'), 'Quality Inspector')
        self.assertEqual(len(found.get('permissions', [])), 4)

        # Verify audit log was created
        audit_events = MockSheetsDB.get_audit_logs()
        role_created_event = next((e for e in audit_events if e.get('Action') == 'ROLE_CREATED'), None)
        self.assertIsNotNone(role_created_event, "Audit log missing for ROLE_CREATED")

    def test_05_update_custom_role_flow(self):
        """Verify custom role can be updated, while system role renaming is blocked."""
        self._login(self.super_admin_email)

        # Create a custom role
        res = self.client.post('/api/rbac/roles', json={
            'role_name': 'Billing Specialist',
            'description': 'Handles billing complaints',
            'data_scope': 'DEPARTMENT',
            'permissions': ['complaints:view']
        })
        self.assertEqual(res.status_code, 201)
        role_id = res.get_json().get('role').get('role_id')

        # Update description and add permissions
        update_payload = {
            'role_name': 'Senior Billing Specialist',
            'description': 'Handles senior billing complaints',
            'data_scope': 'ALL',
            'permissions': ['complaints:view', 'complaints:edit', 'reports:view']
        }
        res_up = self.client.put(f'/api/rbac/roles/{role_id}', json=update_payload)
        self.assertEqual(res_up.status_code, 200)
        up_role = res_up.get_json().get('role')
        self.assertEqual(up_role.get('role_name'), 'Senior Billing Specialist')
        self.assertEqual(up_role.get('data_scope'), 'ALL')
        self.assertEqual(len(up_role.get('permissions', [])), 3)

        # Attempt to rename a system role (Super Admin) - must be rejected
        res_sys = self.client.put('/api/rbac/roles/ROLE-001', json={'role_name': 'God Mode'})
        self.assertEqual(res_sys.status_code, 400)
        self.assertIn("System role", res_sys.get_json().get('message'))

    def test_06_delete_role_safety_protections(self):
        """Verify role deletion protections: cannot delete system role, cannot delete assigned role, can delete unassigned custom role."""
        self._login(self.super_admin_email)

        # Attempt to delete system role (Super Admin)
        res_sys = self.client.delete('/api/rbac/roles/ROLE-001')
        self.assertIn(res_sys.status_code, (400, 409))
        self.assertIn("system", res_sys.get_json().get('message').lower())

        # Create a custom role and assign a user to it
        res_c = self.client.post('/api/rbac/roles', json={
            'role_name': 'Field Agent',
            'data_scope': 'DEPARTMENT',
            'permissions': ['complaints:view']
        })
        self.assertEqual(res_c.status_code, 201)
        field_role_id = res_c.get_json().get('role').get('role_id')

        # Assign user to Field Agent
        MockSheetsDB.create_user(
            full_name='Agent 1',
            email='agent1@ajitzone.local',
            password='Admin@123456',
            role='Field Agent',
            department='Operations'
        )

        # Attempt to delete Field Agent (has 1 assigned user) -> Must be blocked!
        res_del_blocked = self.client.delete(f'/api/rbac/roles/{field_role_id}')
        self.assertIn(res_del_blocked.status_code, (400, 409))
        self.assertIn("assigned to 1 user", res_del_blocked.get_json().get('message').lower())

        # Create an unassigned custom role
        res_unassigned = self.client.post('/api/rbac/roles', json={
            'role_name': 'Temporary Role',
            'data_scope': 'OWN',
            'permissions': ['complaints:view']
        })
        self.assertEqual(res_unassigned.status_code, 201)
        temp_role_id = res_unassigned.get_json().get('role').get('role_id')

        # Delete unassigned custom role -> Must succeed!
        res_del_ok = self.client.delete(f'/api/rbac/roles/{temp_role_id}')
        self.assertEqual(res_del_ok.status_code, 200)

        # Verify it no longer exists
        res_verify = self.client.get(f'/api/rbac/roles/{temp_role_id}')
        self.assertEqual(res_verify.status_code, 404)

    def test_07_rbac_enforcement_on_role_endpoints(self):
        """Verify unauthorized users (e.g. Viewer) cannot modify roles."""
        self._login(self.viewer_email)

        # Viewer tries to create role -> 403 Forbidden
        res_create = self.client.post('/api/rbac/roles', json={'role_name': 'Hacker Role'})
        self.assertEqual(res_create.status_code, 403)

        # Viewer tries to update role -> 403 Forbidden
        res_update = self.client.put('/api/rbac/roles/ROLE-001', json={'role_name': 'Hacker Role'})
        self.assertEqual(res_update.status_code, 403)

        # Viewer tries to delete role -> 403 Forbidden
        res_del = self.client.delete('/api/rbac/roles/ROLE-001')
        self.assertEqual(res_del.status_code, 403)

if __name__ == '__main__':
    unittest.main()
