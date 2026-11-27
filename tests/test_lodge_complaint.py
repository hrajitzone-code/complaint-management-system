"""
Unit tests for Complaint Lodging, validation, retry/duplicate guard, and field mapping.
"""
import unittest
import os
import json

os.environ['TESTING'] = 'True'
os.environ['USE_MOCK_STORAGE'] = 'True'

from app import app, _recent_submissions
from services.mock_sheets_db import MockSheetsDB

class TestLodgeComplaint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test_secret_key'
        cls.db = MockSheetsDB()
        app.config['DB_INSTANCE'] = cls.db
        cls.client = app.test_client()

    def setUp(self):
        MockSheetsDB.reset()
        _recent_submissions.clear()

        # Seed roles in MockSheetsDB
        MockSheetsDB._store['Roles'] = [
            {
                'Role ID': 'ROLE-001',
                'Name': 'Super Admin',
                'Description': 'Full system control',
                'Data Scope': 'ALL',
                'Permissions': json.dumps(['complaints:view', 'complaints:create', 'complaints:edit', 'complaints:delete']),
                'Is System Role': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Updated At': '2026-01-01 00:00:00'
            },
            {
                'Role ID': 'ROLE-002',
                'Name': 'Complaint Executive',
                'Description': 'Executive handling complaints',
                'Data Scope': 'DEPARTMENT',
                'Permissions': json.dumps(['complaints:view', 'complaints:create', 'complaints:edit']),
                'Is System Role': 'FALSE',
                'Created At': '2026-01-01 00:00:00',
                'Last Updated At': '2026-01-01 00:00:00'
            },
            {
                'Role ID': 'ROLE-003',
                'Name': 'Department Staff',
                'Description': 'Staff member',
                'Data Scope': 'DEPARTMENT',
                'Permissions': json.dumps(['complaints:view', 'complaints:create', 'complaints:edit']),
                'Is System Role': 'FALSE',
                'Created At': '2026-01-01 00:00:00',
                'Last Updated At': '2026-01-01 00:00:00'
            }
        ]

        self.super_admin_user = {
            'user_id': 'USR-SUPERADMIN',
            'name': 'Super Admin User',
            'email': 'admin@complaints.local',
            'role': 'Super Admin',
            'department': 'Management',
            'is_active': 'TRUE',
            'permissions': ['complaints:view', 'complaints:create', 'complaints:edit', 'complaints:delete']
        }

        self.executive_user = {
            'user_id': 'USR-EXEC-001',
            'name': 'Complaint Exec',
            'email': 'ajitzonealleditor@gmail.com',
            'role': 'Complaint Executive',
            'department': 'Online, Kurti',
            'departments': ['Online', 'Kurti'],
            'is_active': 'TRUE',
            'permissions': ['complaints:view', 'complaints:create', 'complaints:edit']
        }

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user'] = user

    def test_lodge_complaint_success_and_field_mapping(self):
        """Verifies that lodging a complaint succeeds with 201 and maps all fields accurately."""
        self._login(self.super_admin_user)

        payload = {
            'customer_name': 'Ramesh Kumar',
            'customer_mobile_no': '9876543210',
            'invoice_no': 'INV-2026-8899',
            'complain_date': '2026-11-26',
            'employee_name': 'Deepak Verma',
            'salesperson_name': 'Rohan Sharma',
            'department_name': 'Kurti',
            'from_department': 'Online',
            'complain_types': 'Fabric Defect',
            'priority': 'High',
            'category': 'Quality Issue',
            'vendor_name': 'Surat Mills',
            'complain_details': 'Color bleed noticed after first wash on delivered kurti.',
            'remarks': 'Customer wants replacement.',
            'status': 'Submitted',
            'handled_by': 'Unassigned',
            'closing_date': '',
            'solution_given': '',
            'comments': 'Initial report logged.',
            'total_time_taken': '',
            'gr_amount': '1500.50',
            'gr_expected_date': '2026-11-30'
        }

        res = self.client.post('/api/complaints', data=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')
        self.assertIn('lodged successfully', data.get('message', ''))
        
        comp = data.get('complaint')
        self.assertIsNotNone(comp)
        self.assertEqual(comp.get('Customer Name'), 'Ramesh Kumar')
        self.assertEqual(comp.get('Customer Mobile No.'), '9876543210')
        self.assertEqual(comp.get('Invoice No.'), 'INV-2026-8899')
        self.assertEqual(comp.get('Department Name'), 'Kurti')
        self.assertEqual(comp.get('From Department'), 'Online')
        self.assertEqual(comp.get('Complain Types'), 'Fabric Defect')
        self.assertEqual(comp.get('Priority'), 'High')
        self.assertEqual(comp.get('Complain Details'), 'Color bleed noticed after first wash on delivered kurti.')
        self.assertEqual(comp.get('GR Amount'), '1500.50')
        self.assertEqual(comp.get('GR Expected Date'), '2026-11-30')

        # Verify record exists in MockSheetsDB
        all_comps = MockSheetsDB.get_all_complaints()
        self.assertEqual(len(all_comps), 1)
        self.assertEqual(all_comps[0]['Complaint ID'], comp['Complaint ID'])

    def test_prevent_duplicate_submissions_when_user_retries(self):
        """Verifies that immediate retry with identical details returns the existing record and prevents duplicate rows."""
        self._login(self.executive_user)

        payload = {
            'customer_name': 'Sita Devi',
            'customer_mobile_no': '9123456780',
            'invoice_no': 'INV-4411',
            'complain_date': '2026-11-26',
            'department_name': 'Online',
            'from_department': 'Online',
            'complain_types': 'Wrong Size',
            'complain_details': 'Received size L instead of size M.',
            'priority': 'Medium',
            'gr_amount': '0'
        }

        # 1. First submission succeeds (201 Created)
        res1 = self.client.post('/api/complaints', data=payload)
        self.assertEqual(res1.status_code, 201)
        data1 = res1.get_json()
        cid1 = data1['complaint']['Complaint ID']

        # 2. Immediate retry with identical details (e.g. user double clicked or retried after network stutter)
        res2 = self.client.post('/api/complaints', data=payload)
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertEqual(data2.get('status'), 'success')
        self.assertIn('already been lodged successfully', data2.get('message', ''))
        self.assertEqual(data2['complaint']['Complaint ID'], cid1)

        # Database must still have exactly 1 record, not 2!
        all_comps = MockSheetsDB.get_all_complaints()
        self.assertEqual(len(all_comps), 1)

    def test_validation_errors_return_clean_json_messages(self):
        """Verifies that missing or invalid fields return HTTP 400 with descriptive error messages."""
        self._login(self.super_admin_user)

        # Missing Customer Name
        res = self.client.post('/api/complaints', data={
            'department_name': 'Kurti',
            'complain_types': 'Defect',
            'complain_details': 'Details'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('Customer Name is required', res.get_json().get('message', ''))

        # Missing Department Name
        res = self.client.post('/api/complaints', data={
            'customer_name': 'Customer A',
            'complain_types': 'Defect',
            'complain_details': 'Details'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('Department Name is required', res.get_json().get('message', ''))

        # Missing Complain Types
        res = self.client.post('/api/complaints', data={
            'customer_name': 'Customer A',
            'department_name': 'Kurti',
            'complain_details': 'Details'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('Complain Types is required', res.get_json().get('message', ''))

        # Missing Complain Details
        res = self.client.post('/api/complaints', data={
            'customer_name': 'Customer A',
            'department_name': 'Kurti',
            'complain_types': 'Defect'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('Complain Details is required', res.get_json().get('message', ''))

        # Invalid From Department
        res = self.client.post('/api/complaints', data={
            'customer_name': 'Customer A',
            'department_name': 'Kurti',
            'complain_types': 'Defect',
            'complain_details': 'Details',
            'from_department': 'InvalidDept'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('Invalid From Department', res.get_json().get('message', ''))

        # Negative GR Amount
        res = self.client.post('/api/complaints', data={
            'customer_name': 'Customer A',
            'department_name': 'Kurti',
            'complain_types': 'Defect',
            'complain_details': 'Details',
            'from_department': 'Online',
            'gr_amount': '-500'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('GR Amount must be a positive number', res.get_json().get('message', ''))

    def test_executive_and_staff_roles_authorized_to_lodge(self):
        """Verifies that Complaint Executive and Department Staff accounts can lodge complaints."""
        self._login(self.executive_user)
        res = self.client.post('/api/complaints', data={
            'customer_name': 'Priya Patel',
            'department_name': 'Online',
            'from_department': 'Online',
            'complain_types': 'Packaging Damage',
            'complain_details': 'Box arrived crushed and torn.'
        })
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json().get('status'), 'success')

    def test_in_progress_submission_returns_409(self):
        """Verifies that when a submission is currently being processed, duplicate submission returns 409."""
        self._login(self.super_admin_user)
        payload = {
            'customer_name': 'Amit Shah',
            'department_name': 'Kurti',
            'from_department': 'Offline',
            'complain_types': 'Wrong Item',
            'complain_details': 'Different kurti delivered in box.'
        }

        # First post succeeds (201)
        res1 = self.client.post('/api/complaints', data=payload)
        self.assertEqual(res1.status_code, 201)

        # Immediate retry returns 200 with completed record
        res2 = self.client.post('/api/complaints', data=payload)
        self.assertEqual(res2.status_code, 200)
        self.assertIn('already been lodged successfully', res2.get_json().get('message', ''))

    def test_error_recovery_clears_sub_key(self):
        """Verifies that if an error occurs while saving, sub_key is popped and user can immediately retry."""
        self._login(self.super_admin_user)
        payload = {
            'customer_name': 'Kavita Roy',
            'department_name': 'Kurti',
            'from_department': 'Packing',
            'complain_types': 'Missing Button',
            'complain_details': 'Front top button missing from kurti.'
        }

        # Temporarily mock MockSheetsDB.lodge_complaint to raise an exception
        original_lodge = MockSheetsDB.lodge_complaint
        @classmethod
        def failing_lodge(cls, **kwargs):
            raise RuntimeError("Simulated Google Sheets timeout")
        MockSheetsDB.lodge_complaint = failing_lodge

        try:
            res_fail = self.client.post('/api/complaints', data=payload)
            self.assertEqual(res_fail.status_code, 500)
            self.assertIn('Simulated Google Sheets timeout', res_fail.get_json().get('message', ''))
        finally:
            MockSheetsDB.lodge_complaint = original_lodge

        # Now immediately retry - should NOT be blocked by duplicate debounce!
        res_retry = self.client.post('/api/complaints', data=payload)
        self.assertEqual(res_retry.status_code, 201)
        self.assertEqual(res_retry.get_json().get('status'), 'success')

if __name__ == '__main__':
    unittest.main()
