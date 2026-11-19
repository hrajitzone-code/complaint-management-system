"""
Comprehensive Test Suite for User Creation and Role Assignment
Ajit Zone Pvt. Ltd. - Complaint Management System

Tests:
1. Create user with default role and department without custom permissions.
2. Create user with multi-department assignment.
3. Create user assigned to a custom role created via Role Builder.
4. Verify newly created users can successfully log in via /api/auth/login.
5. Verify effective permissions and department scoping in user session.
6. Privilege escalation guard: Admin cannot create Super Admin (403). Super Admin can.
7. Validation errors: duplicate email (400), short password (400), missing required fields (400).
8. Rechecking Role Builder Create, Edit, and Delete functionality for complete zero regression.
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

class TestUserCreation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        Config.INITIAL_ADMIN_PASSWORD = 'Admin@123456'

    def setUp(self):
        MockSheetsDB.reset()
        self.client = app.test_client()

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

    def test_01_create_user_basic(self):
        """Verify Admin/Super Admin creates user with Department Staff role and department."""
        self._login(self.super_admin_email)

        payload = {
            'full_name': 'Rahul Sharma',
            'email': 'rahul.sharma@ajitzone.local',
            'password': 'Password@123',
            'role': 'Department Staff',
            'department': 'Packing'
        }
        res = self.client.post('/api/users', json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')
        new_user = data.get('user', {})
        self.assertEqual(new_user.get('Full Name') or new_user.get('full_name'), 'Rahul Sharma')
        self.assertEqual(new_user.get('Email') or new_user.get('email'), 'rahul.sharma@ajitzone.local')
        self.assertEqual(new_user.get('Role') or new_user.get('role'), 'Department Staff')
        self.assertEqual(new_user.get('Department') or new_user.get('department'), 'Packing')

        # Now test that newly created user can log in
        login_res = self.client.post('/api/auth/login', json={
            'email': 'rahul.sharma@ajitzone.local',
            'password': 'Password@123'
        })
        self.assertEqual(login_res.status_code, 200)
        session_user = login_res.get_json().get('user', {})
        self.assertEqual(session_user.get('email'), 'rahul.sharma@ajitzone.local')
        self.assertEqual(session_user.get('role'), 'Department Staff')
        self.assertIn('complaints:view', session_user.get('permissions', []))
        self.assertIn('complaints:edit', session_user.get('permissions', []))
        self.assertEqual(session_user.get('data_scope'), 'DEPARTMENT')
        self.assertIn('Packing', session_user.get('departments', []))

    def test_02_create_user_multi_department(self):
        """Verify creating a user with multiple departments assigned."""
        self._login(self.super_admin_email)

        payload = {
            'full_name': 'Pooja Patel',
            'email': 'pooja.patel@ajitzone.local',
            'password': 'Password@123',
            'role': 'Department Head',
            'department': 'Online, Kurti, Saree'
        }
        res = self.client.post('/api/users', json=payload)
        self.assertEqual(res.status_code, 201)

        # Log in and check department list
        login_res = self.client.post('/api/auth/login', json={
            'email': 'pooja.patel@ajitzone.local',
            'password': 'Password@123'
        })
        self.assertEqual(login_res.status_code, 200)
        session_user = login_res.get_json().get('user', {})
        self.assertEqual(set(session_user.get('departments', [])), {'Online', 'Kurti', 'Saree'})

    def test_03_create_user_assigned_to_custom_role(self):
        """Verify creating a custom role via Role Builder, then creating a user assigned to it."""
        self._login(self.super_admin_email)

        # 1. Create custom role
        role_payload = {
            'role_name': 'Dispatch Officer',
            'description': 'Handles order dispatch and shipping complaints',
            'data_scope': 'DEPARTMENT',
            'permissions': ['complaints:view', 'complaints:edit', 'comments:view', 'comments:add']
        }
        r_res = self.client.post('/api/rbac/roles', json=role_payload)
        self.assertEqual(r_res.status_code, 201)

        # 2. Create user with custom role
        user_payload = {
            'full_name': 'Ankit Verma',
            'email': 'ankit.verma@ajitzone.local',
            'password': 'Password@123',
            'role': 'Dispatch Officer',
            'department': 'Packing'
        }
        u_res = self.client.post('/api/users', json=user_payload)
        self.assertEqual(u_res.status_code, 201)

        # 3. Verify user login and effective permissions from custom role
        login_res = self.client.post('/api/auth/login', json={
            'email': 'ankit.verma@ajitzone.local',
            'password': 'Password@123'
        })
        self.assertEqual(login_res.status_code, 200)
        session_user = login_res.get_json().get('user', {})
        self.assertEqual(session_user.get('role'), 'Dispatch Officer')
        self.assertIn('complaints:view', session_user.get('permissions', []))
        self.assertIn('comments:add', session_user.get('permissions', []))
        self.assertNotIn('users:view', session_user.get('permissions', []))

    def test_04_privilege_escalation_guard(self):
        """Admin cannot create Super Admin user; Super Admin can create any role."""
        # Log in as Admin
        self._login(self.admin_email)

        # Admin attempts to create Super Admin user -> 403 Forbidden
        payload = {
            'full_name': 'Escalated User',
            'email': 'escalated@ajitzone.local',
            'password': 'Password@123',
            'role': 'Super Admin',
            'department': 'Management'
        }
        res = self.client.post('/api/users', json=payload)
        self.assertEqual(res.status_code, 403)
        self.assertIn("Only Super Admins", res.get_json().get('message', ''))

        # Log in as Super Admin -> Super Admin CAN create Super Admin
        self._login(self.super_admin_email)
        res_ok = self.client.post('/api/users', json=payload)
        self.assertEqual(res_ok.status_code, 201)

    def test_05_validation_errors(self):
        """Verify email uniqueness, short password, and missing required fields."""
        self._login(self.super_admin_email)

        # Missing required field
        res1 = self.client.post('/api/users', json={'email': 'missing@test.local', 'password': 'Password@123'})
        self.assertEqual(res1.status_code, 400)

        # Short password (<6 chars)
        res2 = self.client.post('/api/users', json={
            'full_name': 'Short Pwd',
            'email': 'short@test.local',
            'password': '12345',
            'role': 'User',
            'department': 'General'
        })
        self.assertEqual(res2.status_code, 400)
        self.assertIn("at least 6 characters", res2.get_json().get('message', ''))

        # Duplicate email
        res3 = self.client.post('/api/users', json={
            'full_name': 'Duplicate User',
            'email': self.admin_email,
            'password': 'Password@123',
            'role': 'User',
            'department': 'General'
        })
        self.assertEqual(res3.status_code, 400)
        self.assertIn("already exists", res3.get_json().get('message', ''))

    def test_06_role_builder_recheck(self):
        """Recheck Role Builder create, edit, usage, and delete with live user assignment."""
        self._login(self.super_admin_email)

        # Create custom role
        res = self.client.post('/api/rbac/roles', json={
            'role_name': 'Inventory Specialist',
            'description': 'Handles inventory queries',
            'data_scope': 'DEPARTMENT',
            'permissions': ['complaints:view']
        })
        self.assertEqual(res.status_code, 201)
        role_id = res.get_json().get('role', {}).get('role_id')

        # Edit custom role
        res_edit = self.client.put(f'/api/rbac/roles/{role_id}', json={
            'role_name': 'Senior Inventory Specialist',
            'description': 'Updated description',
            'data_scope': 'ALL',
            'permissions': ['complaints:view', 'reports:view']
        })
        self.assertEqual(res_edit.status_code, 200)

        # Assign a user to it
        res_u = self.client.post('/api/users', json={
            'full_name': 'Inv User',
            'email': 'inv@ajitzone.local',
            'password': 'Password@123',
            'role': 'Senior Inventory Specialist',
            'department': 'Packing'
        })
        self.assertEqual(res_u.status_code, 201)

        # Role usage check -> must report 1 assigned user and can_delete=False
        usage_res = self.client.get(f'/api/rbac/roles/{role_id}/usage')
        self.assertEqual(usage_res.status_code, 200)
        u_data = usage_res.get_json()
        self.assertEqual(u_data.get('assigned_users_count'), 1)
        self.assertFalse(u_data.get('can_delete'))

        # Delete must be blocked because user is assigned
        del_res = self.client.delete(f'/api/rbac/roles/{role_id}')
        self.assertIn(del_res.status_code, (400, 409))

if __name__ == '__main__':
    unittest.main()
