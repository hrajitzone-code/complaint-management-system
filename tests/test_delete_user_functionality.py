import os
import sys
import unittest
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ['TESTING'] = 'True'

from unittest.mock import patch
from app import app
from services.mock_sheets_db import MockSheetsDB

class TestDeleteUserFunctionality(unittest.TestCase):
    def setUp(self):
        MockSheetsDB.reset()
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        # Seed test users in Mock store
        # USR-0001: Super Admin 1
        # USR-0002: Admin 1
        # USR-0003: Staff 1
        # USR-0004: Regular User 1
        # USR-0005: Super Admin 2
        MockSheetsDB._store['Users'] = [
            {
                'User ID': 'USR-0001',
                'Full Name': 'Super Admin One',
                'Email': 'superadmin1@example.com',
                'Password Hash': 'pbkdf2:sha256:dummyhash',
                'Role': 'Super Admin',
                'Department': 'Executive',
                'Is Active': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Login': ''
            },
            {
                'User ID': 'USR-0002',
                'Full Name': 'Admin User',
                'Email': 'admin@example.com',
                'Password Hash': 'pbkdf2:sha256:dummyhash',
                'Role': 'Admin',
                'Department': 'Operations',
                'Is Active': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Login': ''
            },
            {
                'User ID': 'USR-0003',
                'Full Name': 'Staff Member',
                'Email': 'staff@example.com',
                'Password Hash': 'pbkdf2:sha256:dummyhash',
                'Role': 'Support Staff',
                'Department': 'IT & Technical Support',
                'Is Active': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Login': ''
            },
            {
                'User ID': 'USR-0004',
                'Full Name': 'Customer User',
                'Email': 'customer@example.com',
                'Password Hash': 'pbkdf2:sha256:dummyhash',
                'Role': 'User',
                'Department': 'General',
                'Is Active': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Login': ''
            },
            {
                'User ID': 'USR-0005',
                'Full Name': 'Super Admin Two',
                'Email': 'superadmin2@example.com',
                'Password Hash': 'pbkdf2:sha256:dummyhash',
                'Role': 'Super Admin',
                'Department': 'Executive',
                'Is Active': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Login': ''
            }
        ]

        # Seed sample complaints to test activity dependencies & preservation
        MockSheetsDB._store['Complaints'] = [
            {
                'Complaint ID': 'CMP-1001',
                'Complainant Email': 'customer@example.com',
                'Employee Name': 'Customer User',
                'Handled By': 'Staff Member',
                'Status': 'In Progress'
            },
            {
                'Complaint ID': 'CMP-1002',
                'Complainant Email': 'other@example.com',
                'Employee Name': 'Other User',
                'Handled By': 'staff@example.com',
                'Status': 'Resolved'
            }
        ]

        MockSheetsDB._store['Assignments and Comments'] = [
            {
                'Comment ID': 'COM-101',
                'Complaint ID': 'CMP-1001',
                'Author Email': 'staff@example.com',
                'Author Name': 'Staff Member',
                'Message': 'Investigating issue'
            }
        ]

        MockSheetsDB._store['Status History'] = []
        MockSheetsDB._store['System_Config'] = []

    def _login_as(self, user_id, email, role, name='Test User'):
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'user_id': user_id,
                'email': email,
                'role': role,
                'name': name,
                'is_active': 'TRUE'
            }

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_user_usage_analysis_endpoint(self, mock_db):
        """Verify GET /api/users/<user_id>/usage returns correct complaint & comment dependency counts."""
        self._login_as('USR-0002', 'admin@example.com', 'Admin')

        # Test customer user dependencies
        res = self.client.get('/api/users/USR-0004/usage')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['usage']['lodged_complaints_count'], 1)
        self.assertEqual(data['usage']['handled_complaints_count'], 0)

        # Test staff member dependencies
        res_staff = self.client.get('/api/users/USR-0003/usage')
        self.assertEqual(res_staff.status_code, 200)
        data_staff = res_staff.get_json()
        self.assertEqual(data_staff['usage']['handled_complaints_count'], 2)
        self.assertEqual(data_staff['usage']['comments_count'], 1)

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_admin_can_soft_delete_user(self, mock_db):
        """Admin can soft-delete (archive) a regular user/staff with a valid reason."""
        self._login_as('USR-0002', 'admin@example.com', 'Admin', 'Admin User')

        res = self.client.delete('/api/users/USR-0004', json={
            'action': 'soft',
            'reason': 'Employee departed organization.'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['action'], 'soft')

        # Verify user is marked ARCHIVED in database
        user = MockSheetsDB.find_user_by_id('USR-0004')
        self.assertIsNotNone(user)
        self.assertEqual(user['Is Active'], 'ARCHIVED')

        # Verify complaints still exist completely untouched
        complaints = MockSheetsDB.get_complaints_for_user('customer@example.com', 'Super Admin')
        self.assertTrue(len(complaints) > 0)

        # Verify Status History audit trail
        history = MockSheetsDB._store['Status History']
        self.assertTrue(len(history) > 0)
        user_history = [h for h in history if 'USER:USR-0004' in h.get('Complaint ID', '')]
        self.assertEqual(len(user_history), 1)
        self.assertEqual(user_history[0]['New Status'], 'ARCHIVED')
        self.assertIn('Employee departed', user_history[0]['Remarks'])

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_archived_user_cannot_access_protected_endpoints(self, mock_db):
        """An archived user's session is cleared and denied with 403."""
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'user_id': 'USR-0004',
                'email': 'customer@example.com',
                'role': 'User',
                'name': 'Customer User',
                'is_active': 'ARCHIVED'
            }

        res = self.client.get('/api/complaints')
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertIn('disabled or archived', data['message'].lower())

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_admin_cannot_delete_or_archive_super_admin(self, mock_db):
        """Admins cannot soft-delete or permanent-delete a Super Admin (HTTP 403)."""
        self._login_as('USR-0002', 'admin@example.com', 'Admin')

        # Attempt soft delete
        res_soft = self.client.delete('/api/users/USR-0001', json={
            'action': 'soft',
            'reason': 'Trying to archive super admin'
        })
        self.assertEqual(res_soft.status_code, 403)

        # Attempt permanent delete
        res_perm = self.client.delete('/api/users/USR-0001', json={
            'action': 'permanent',
            'confirm': 'DELETE',
            'reason': 'Trying to delete super admin'
        })
        self.assertEqual(res_perm.status_code, 403)

        # Ensure Super Admin remains active
        super_user = MockSheetsDB.find_user_by_id('USR-0001')
        self.assertEqual(super_user['Is Active'], 'TRUE')

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_admin_cannot_permanently_delete_any_user(self, mock_db):
        """Only Super Admins can permanently delete accounts. Admin gets 403."""
        self._login_as('USR-0002', 'admin@example.com', 'Admin')

        res = self.client.delete('/api/users/USR-0004', json={
            'action': 'permanent',
            'confirm': 'DELETE',
            'reason': 'Admin trying permanent purge'
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn('strictly restricted to super admins', res.get_json()['message'].lower())

        # Ensure user was not deleted
        user = MockSheetsDB.find_user_by_id('USR-0004')
        self.assertIsNotNone(user)

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_super_admin_can_permanently_delete_user(self, mock_db):
        """Super Admin can permanently delete an account with reason & confirm='DELETE'."""
        self._login_as('USR-0001', 'superadmin1@example.com', 'Super Admin')

        res = self.client.delete('/api/users/USR-0004', json={
            'action': 'permanent',
            'confirm': 'DELETE',
            'reason': 'GDPR Data Erasure Request'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['action'], 'permanent')

        # User row is removed from Users sheet
        self.assertIsNone(MockSheetsDB.find_user_by_id('USR-0004'))

        # Historical complaint CMP-1001 is STILL PRESERVED!
        c = MockSheetsDB.get_complaint_by_id('CMP-1001')
        self.assertIsNotNone(c)
        self.assertEqual(c['Complainant Email'], 'customer@example.com')

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_self_deletion_is_blocked(self, mock_db):
        """Users cannot delete or archive their own account (HTTP 400)."""
        self._login_as('USR-0001', 'superadmin1@example.com', 'Super Admin')

        res = self.client.delete('/api/users/USR-0001', json={
            'action': 'soft',
            'reason': 'Suicide attempt'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('cannot delete or archive your own', res.get_json()['message'].lower())

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_cannot_delete_last_super_admin(self, mock_db):
        """Cannot delete or archive the sole remaining active Super Admin."""
        # Deactivate USR-0005 so only USR-0001 is active
        MockSheetsDB.update_user('USR-0005', is_active='FALSE')
        self.assertEqual(MockSheetsDB.count_active_super_admins(), 1)

        # Login as another admin (or mock session) attempting to target USR-0001
        self._login_as('USR-0005', 'superadmin2@example.com', 'Super Admin')

        res = self.client.delete('/api/users/USR-0001', json={
            'action': 'soft',
            'reason': 'Archiving sole active super admin'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('sole active super admin', res.get_json()['message'].lower())

    @patch('app.get_db', return_value=MockSheetsDB)
    def test_mandatory_reason_and_confirm_checks(self, mock_db):
        """Missing reason returns 400; permanent without 'DELETE' returns 400."""
        self._login_as('USR-0001', 'superadmin1@example.com', 'Super Admin')

        # Missing reason
        res_no_reason = self.client.delete('/api/users/USR-0004', json={
            'action': 'soft',
            'reason': ''
        })
        self.assertEqual(res_no_reason.status_code, 400)
        self.assertIn('reason', res_no_reason.get_json()['message'].lower())

        # Permanent without DELETE confirmation
        res_no_confirm = self.client.delete('/api/users/USR-0004', json={
            'action': 'permanent',
            'reason': 'Valid reason',
            'confirm': 'wrong'
        })
        self.assertEqual(res_no_confirm.status_code, 400)
        self.assertIn('typing delete', res_no_confirm.get_json()['message'].lower())

if __name__ == '__main__':
    unittest.main()
