"""
Unit and RBAC Tests for User Management – Edit User Functionality.
Uses in-memory MockSheetsDB to ensure zero writes or alterations to production Google Sheets.
"""
import os
import sys
import unittest

# Force testing mode with in-memory mock storage
os.environ['TESTING'] = 'True'
os.environ['USE_MOCK_STORAGE'] = 'True'

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app
from services.mock_sheets_db import MockSheetsDB
from werkzeug.security import check_password_hash

class TestEditUserFunctionality(unittest.TestCase):
    def setUp(self):
        MockSheetsDB.reset()
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test_secret_key'
        self.client = self.app.test_client()

        # Seed mock users in memory
        MockSheetsDB.create_user(
            full_name="Super Administrator",
            email="superadmin@test.local",
            password="Password@123",
            role="Super Admin",
            department="Management"
        )
        MockSheetsDB.create_user(
            full_name="Standard Admin",
            email="admin@test.local",
            password="Password@123",
            role="Admin",
            department="Management"
        )
        MockSheetsDB.create_user(
            full_name="Standard Staff",
            email="staff@test.local",
            password="Password@123",
            role="Support Staff",
            department="Operations"
        )
        MockSheetsDB.create_user(
            full_name="Regular Complainant",
            email="user@test.local",
            password="Password@123",
            role="User",
            department="General"
        )

    def _login(self, email, password="Password@123"):
        return self.client.post('/api/auth/login', json={'email': email, 'password': password})

    def test_01_get_user_details_excluding_password_hash(self):
        """Verify GET /api/users/<user_id> returns safe details without password hash."""
        self._login("superadmin@test.local")
        resp = self.client.get('/api/users/USR-0001')
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertEqual(data['status'], 'success')
        user = data['user']
        self.assertEqual(user['User ID'], 'USR-0001')
        self.assertEqual(user['Full Name'], 'Super Administrator')
        self.assertNotIn('Password Hash', user)
        self.assertNotIn('password', user)

    def test_02_super_admin_can_edit_user(self):
        """Verify Super Admin can update user details and account status."""
        self._login("superadmin@test.local")
        update_payload = {
            "full_name": "Updated Staff Member",
            "email": "updated.staff@test.local",
            "role": "Support Staff",
            "department": "Finance & Billing",
            "is_active": "TRUE"
        }
        resp = self.client.put('/api/users/USR-0003', json=update_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['user']['Full Name'], 'Updated Staff Member')
        self.assertEqual(data['user']['Email'], 'updated.staff@test.local')
        self.assertEqual(data['user']['Department'], 'Finance & Billing')

    def test_03_email_uniqueness_validation(self):
        """Verify updating a user to an already-registered email is rejected with 400."""
        self._login("superadmin@test.local")
        # Try to change USR-0003 email to USR-0002's email
        update_payload = {
            "full_name": "Updated Staff",
            "email": "admin@test.local", # already taken by USR-0002
            "role": "Support Staff",
            "department": "Operations",
            "is_active": "TRUE"
        }
        resp = self.client.put('/api/users/USR-0003', json=update_payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already in use", resp.json.get('message', '').lower())

    def test_04_privilege_escalation_prevention(self):
        """Verify standard Admin cannot edit a Super Admin or promote anyone to Super Admin."""
        self._login("admin@test.local")

        # 1. Admin trying to edit Super Admin
        resp1 = self.client.put('/api/users/USR-0001', json={
            "full_name": "Hacked Super",
            "email": "superadmin@test.local",
            "role": "Super Admin",
            "department": "Management"
        })
        self.assertEqual(resp1.status_code, 403)
        self.assertIn("Only Super Admins can edit Super Admin accounts", resp1.json.get('message', ''))

        # 2. Admin trying to elevate a regular user to Super Admin
        resp2 = self.client.put('/api/users/USR-0004', json={
            "full_name": "Elevated User",
            "email": "user@test.local",
            "role": "Super Admin",
            "department": "General"
        })
        self.assertEqual(resp2.status_code, 403)
        self.assertIn("Only Super Admins can assign the Super Admin role", resp2.json.get('message', ''))

    def test_05_self_deactivation_prevention(self):
        """Verify users cannot deactivate their own active account."""
        self._login("superadmin@test.local")
        resp = self.client.put('/api/users/USR-0001', json={
            "full_name": "Super Administrator",
            "email": "superadmin@test.local",
            "role": "Super Admin",
            "department": "Management",
            "is_active": "FALSE"
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot deactivate your own active account", resp.json.get('message', '').lower())

    def test_06_secure_password_reset(self):
        """Verify resetting a user's password updates the hash and allows login with new credential."""
        self._login("superadmin@test.local")
        resp = self.client.put('/api/users/USR-0004', json={
            "full_name": "Regular Complainant",
            "email": "user@test.local",
            "role": "User",
            "department": "General",
            "is_active": "TRUE",
            "new_password": "NewSecretPassword@999"
        })
        self.assertEqual(resp.status_code, 200)

        # Verify old password fails
        login_fail = self._login("user@test.local", "Password@123")
        self.assertEqual(login_fail.status_code, 401)

        # Verify new password succeeds
        login_ok = self._login("user@test.local", "NewSecretPassword@999")
        self.assertEqual(login_ok.status_code, 200)

    def test_07_inactive_user_cannot_login_or_access_apis(self):
        """Verify deactivating an account prevents login and revokes access."""
        self._login("superadmin@test.local")
        # Deactivate USR-0004
        resp = self.client.put('/api/users/USR-0004', json={
            "full_name": "Regular Complainant",
            "email": "user@test.local",
            "role": "User",
            "department": "General",
            "is_active": "FALSE"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json['user']['Is Active'], 'FALSE')

        # Attempt to log in with valid credentials
        login_resp = self._login("user@test.local", "Password@123")
        self.assertEqual(login_resp.status_code, 401)
        self.assertIn("disabled", login_resp.json.get('message', '').lower())

    def test_08_rbac_non_admin_cannot_edit_users(self):
        """Verify regular users and support staff cannot access the edit user endpoint."""
        self._login("user@test.local")
        resp = self.client.put('/api/users/USR-0003', json={"full_name": "Test"})
        self.assertEqual(resp.status_code, 403)

        self._login("staff@test.local")
        resp_staff = self.client.put('/api/users/USR-0003', json={"full_name": "Test"})
        self.assertEqual(resp_staff.status_code, 403)

if __name__ == '__main__':
    unittest.main()
