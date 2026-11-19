"""
Tests for Department Authorization, Multi-Department Scoping, Handled By matching,
and Cross-Department Security Isolation in AJIT ZONE PVT. LTD. Complaint Management System.
"""

import os
import sys
import unittest
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import Config
from app import app
from services.mock_sheets_db import MockSheetsDB
from services.rbac_service import RBACService


class TestDepartmentAuthorization(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        Config.INITIAL_ADMIN_PASSWORD = 'Admin@123456'

    def setUp(self):
        MockSheetsDB.reset()
        self.app = app
        self.client = self.app.test_client()

        # Custom roles including Complaint Executive
        self.custom_roles = [
            {
                'Role ID': 'ROLE-007',
                'Name': 'Complaint Executive',
                'Description': 'Executive handling company complaints',
                'Data Scope': 'DEPARTMENT',
                'Permissions': [
                    'complaints:view', 'complaints:create', 'complaints:edit',
                    'complaints:assign', 'complaints:change_status', 'complaints:close',
                    'complaints:reopen', 'comments:view', 'comments:add'
                ],
                'Is System Role': 'FALSE'
            }
        ]

    # 1. Single Department Staff
    def test_single_department_staff_edit_authorized(self):
        user = {
            'user_id': 'USR-101',
            'name': 'Pooja Patel',
            'email': 'pooja@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Kurti',
            'is_active': 'TRUE'
        }
        complaint = {
            'Complaint ID': 'CMP-001',
            'Department Name': 'Kurti',
            'From Department': 'Offline',
            'Handled By': 'Unassigned',
            'Complainant Email': 'customer@example.com',
            'Status': 'Under Review'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit', custom_roles=self.custom_roles)
        self.assertTrue(allowed)
        self.assertIn("Authorized by department scope", reason)

    def test_single_department_staff_edit_unauthorized_blocked(self):
        user = {
            'user_id': 'USR-101',
            'name': 'Pooja Patel',
            'email': 'pooja@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Kurti',
            'is_active': 'TRUE'
        }
        complaint = {
            'Complaint ID': 'CMP-002',
            'Department Name': 'Saree',
            'From Department': 'Offline',
            'Handled By': 'Unassigned',
            'Complainant Email': 'customer@example.com',
            'Status': 'Under Review'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit', custom_roles=self.custom_roles)
        self.assertFalse(allowed)
        self.assertIn("Complaint does not belong to your authorized department(s)", reason)

    # 2. Multi-Department Staff
    def test_multi_department_staff_edit_authorized(self):
        user = {
            'user_id': 'USR-102',
            'name': 'Ramesh Kumar',
            'email': 'ramesh@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Kurti, Top, Readymade',
            'is_active': 'TRUE'
        }

        # Should be able to edit Kurti
        c_kurti = {'Complaint ID': 'C-1', 'Department Name': 'Kurti', 'Handled By': '', 'Complainant Email': 'c@ex.com'}
        allowed, _ = RBACService.can_access_complaint(user, c_kurti, action='edit')
        self.assertTrue(allowed)

        # Should be able to edit Top
        c_top = {'Complaint ID': 'C-2', 'Department Name': 'Top', 'Handled By': '', 'Complainant Email': 'c@ex.com'}
        allowed, _ = RBACService.can_access_complaint(user, c_top, action='edit')
        self.assertTrue(allowed)

        # Should be able to edit Readymade
        c_rm = {'Complaint ID': 'C-3', 'Department Name': 'Readymade', 'Handled By': '', 'Complainant Email': 'c@ex.com'}
        allowed, _ = RBACService.can_access_complaint(user, c_rm, action='edit')
        self.assertTrue(allowed)

        # Should be BLOCKED for Saree
        c_saree = {'Complaint ID': 'C-4', 'Department Name': 'Saree', 'Handled By': '', 'Complainant Email': 'c@ex.com'}
        allowed, reason = RBACService.can_access_complaint(user, c_saree, action='edit')
        self.assertFalse(allowed)
        self.assertIn("Complaint does not belong to your authorized department(s)", reason)

    # 3. From Department Scoping
    def test_from_department_matching(self):
        user = {
            'user_id': 'USR-103',
            'name': 'Online Agent',
            'email': 'online@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Online',
            'is_active': 'TRUE'
        }
        # Complaint target department is Kurti, but From Department is Online
        complaint = {
            'Complaint ID': 'CMP-003',
            'Department Name': 'Kurti',
            'From Department': 'Online',
            'Handled By': 'Unassigned',
            'Complainant Email': 'customer@example.com',
            'Status': 'Under Review'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit')
        self.assertTrue(allowed)
        self.assertIn("Authorized by department scope", reason)

    # 4. Handled By matching rules
    def test_handled_by_matching_first_name(self):
        """'NITA' in complaint Handled By matches user 'Nita Chaudhary'."""
        user = {
            'user_id': 'USR-0003',
            'name': 'Nita Chaudhary',
            'email': 'ajitzonealleditor@gmail.com',
            'role': 'Complaint Executive',
            'department': 'Complaint',
            'is_active': 'TRUE'
        }
        # Complaint belongs to Kids department, but Handled By is NITA
        complaint = {
            'Complaint ID': 'CMP-004',
            'Department Name': 'Kids',
            'From Department': 'Offline',
            'Handled By': 'NITA',
            'Complainant Email': 'other@example.com',
            'Status': 'Under Review'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit', custom_roles=self.custom_roles)
        self.assertTrue(allowed)
        self.assertIn("Authorized as assigned complaint handler", reason)

    def test_handled_by_matching_email_and_multiple(self):
        user = {
            'user_id': 'USR-104',
            'name': 'Vikram Singh',
            'email': 'vikram@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Bottom',
            'is_active': 'TRUE'
        }
        # Handled By contains multiple handlers
        complaint = {
            'Complaint ID': 'CMP-005',
            'Department Name': 'Dress Material',
            'From Department': 'None',
            'Handled By': 'Nita, Vikram',
            'Complainant Email': 'c@ex.com',
            'Status': 'Under Review'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit')
        self.assertTrue(allowed)
        self.assertIn("Authorized as assigned complaint handler", reason)

    # 5. Staff Owner Creator Edit
    def test_staff_complaint_owner_edit(self):
        user = {
            'user_id': 'USR-0003',
            'name': 'Nita Chaudhary',
            'email': 'ajitzonealleditor@gmail.com',
            'role': 'Complaint Executive',
            'department': 'Complaint',
            'is_active': 'TRUE'
        }
        complaint = {
            'Complaint ID': 'CMP-006',
            'Department Name': 'Kids',
            'From Department': 'Offline',
            'Handled By': 'Pending',
            'Complainant Email': 'ajitzonealleditor@gmail.com',
            'Status': 'Under Review'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit', custom_roles=self.custom_roles)
        self.assertTrue(allowed)
        self.assertIn("Authorized to edit own complaint", reason)

    # 6. Regular User Constraints
    def test_regular_user_submitted_vs_in_progress(self):
        user = {
            'user_id': 'USR-105',
            'name': 'John Customer',
            'email': 'john@example.com',
            'role': 'User',
            'department': 'General',
            'is_active': 'TRUE'
        }
        # In Submitted status: allowed
        c_sub = {
            'Complaint ID': 'CMP-007',
            'Department Name': 'Kurti',
            'Complainant Email': 'john@example.com',
            'Status': 'Submitted'
        }
        allowed, _ = RBACService.can_access_complaint(user, c_sub, action='edit')
        self.assertTrue(allowed)

        # In In Progress status: regular user blocked from editing
        c_prog = {
            'Complaint ID': 'CMP-008',
            'Department Name': 'Kurti',
            'Complainant Email': 'john@example.com',
            'Status': 'In Progress'
        }
        allowed, reason = RBACService.can_access_complaint(user, c_prog, action='edit')
        self.assertFalse(allowed)
        self.assertIn("Only 'Submitted' complaints can be edited by regular users", reason)

    # 7. Viewer role cannot edit
    def test_viewer_role_cannot_edit(self):
        user = {
            'user_id': 'USR-106',
            'name': 'Auditor Viewer',
            'email': 'auditor@ajitzone.com',
            'role': 'Viewer',
            'department': 'Kurti',
            'is_active': 'TRUE'
        }
        complaint = {
            'Complaint ID': 'CMP-009',
            'Department Name': 'Kurti',
            'Complainant Email': 'c@ex.com',
            'Status': 'Submitted'
        }
        # Viewer lacks complaints:edit permission
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit')
        self.assertFalse(allowed)
        self.assertIn("Missing 'complaints:edit'", reason)

    # 8. Super Admin Unrestricted Access
    def test_super_admin_unrestricted_edit(self):
        user = {
            'user_id': 'USR-0001',
            'name': 'System Administrator',
            'email': 'admin@complaints.local',
            'role': 'Super Admin',
            'department': 'Management',
            'is_active': 'TRUE'
        }
        complaint = {
            'Complaint ID': 'CMP-010',
            'Department Name': 'Any Department',
            'Complainant Email': 'random@example.com',
            'Status': 'Closed'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit')
        self.assertTrue(allowed)
        self.assertIn("Authorized as Super Admin", reason)

    # 9. Inactive Account Denied
    def test_inactive_account_denied(self):
        user = {
            'user_id': 'USR-107',
            'name': 'Former Staff',
            'email': 'former@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Kurti',
            'is_active': 'FALSE'
        }
        complaint = {
            'Complaint ID': 'CMP-011',
            'Department Name': 'Kurti',
            'Complainant Email': 'c@ex.com',
            'Status': 'Submitted'
        }
        allowed, reason = RBACService.can_access_complaint(user, complaint, action='edit')
        self.assertFalse(allowed)
        self.assertIn("Account is inactive or archived", reason)

    # 10. Filter complaints by scope with multi-departments
    def test_filter_complaints_by_scope_multi_department(self):
        user = {
            'user_id': 'USR-108',
            'name': 'Priya Staff',
            'email': 'priya@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Kurti, Top',
            'is_active': 'TRUE'
        }
        complaints = [
            {'Complaint ID': 'C-1', 'Department Name': 'Kurti', 'Complainant Email': 'c1@ex.com'},
            {'Complaint ID': 'C-2', 'Department Name': 'Top', 'Complainant Email': 'c2@ex.com'},
            {'Complaint ID': 'C-3', 'Department Name': 'Saree', 'Complainant Email': 'c3@ex.com'},
            {'Complaint ID': 'C-4', 'Department Name': 'Saree', 'Handled By': 'Priya', 'Complainant Email': 'c4@ex.com'},
            {'Complaint ID': 'C-5', 'Department Name': 'Kids', 'From Department': 'Kurti', 'Complainant Email': 'c5@ex.com'}
        ]
        filtered = RBACService.filter_complaints_by_scope(complaints, user)
        filtered_ids = [c['Complaint ID'] for c in filtered]
        self.assertIn('C-1', filtered_ids) # Kurti matches
        self.assertIn('C-2', filtered_ids) # Top matches
        self.assertNotIn('C-3', filtered_ids) # Saree not matched, not handler
        self.assertIn('C-4', filtered_ids) # Saree matched via Handled By 'Priya'
        self.assertIn('C-5', filtered_ids) # From Department 'Kurti' matches

    # 11. Flask API Integration: PUT /api/complaints/<id>
    def test_api_update_complaint_department_authorization(self):
        # Seed complaint in mock store
        c_record = {
            'Complaint ID': 'CMP-TEST-99',
            'Timestamp': '2026-11-19 10:00:00',
            'Complain Date': '2026-11-19',
            'Complainant Name': 'Customer One',
            'Customer Name': 'Customer One',
            'Complainant Email': 'cust@example.com',
            'Customer Mobile No.': '9876543210',
            'Department Name': 'Kurti',
            'From Department': 'Offline',
            'Complain Types': 'Defective Stitching',
            'Priority': 'Medium',
            'Status': 'Under Review',
            'Handled By': 'Unassigned',
            'Employee Name': 'Emp1',
            'Salesperson Name': 'Direct',
            'Invoice No.': 'INV-99'
        }
        MockSheetsDB._store['Complaints'].append(c_record)

        # Case A: Authorized staff in 'Kurti' updates complaint -> 200 OK
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'user_id': 'USR-AUTH-1',
                'name': 'Kurti Staff',
                'email': 'kurtistaff@ajitzone.com',
                'role': 'Department Staff',
                'department': 'Kurti',
                'departments': ['Kurti'],
                'permissions': ['complaints:view', 'complaints:edit'],
                'data_scope': 'DEPARTMENT',
                'is_active': 'TRUE'
            }

        res = self.client.put('/api/complaints/CMP-TEST-99', json={
            'remarks': 'Inspected garment, preparing replacement.',
            'status': 'In Progress'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')

        # Case B: Unauthorized staff in 'Saree' attempts to update complaint -> 403 Forbidden
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'user_id': 'USR-UNAUTH-1',
                'name': 'Saree Staff',
                'email': 'sareestaff@ajitzone.com',
                'role': 'Department Staff',
                'department': 'Saree',
                'departments': ['Saree'],
                'permissions': ['complaints:view', 'complaints:edit'],
                'data_scope': 'DEPARTMENT',
                'is_active': 'TRUE'
            }

        res = self.client.put('/api/complaints/CMP-TEST-99', json={
            'remarks': 'Attempting unauthorized cross-department edit'
        })
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'error')
        self.assertIn("Complaint does not belong to your authorized department(s)", data.get('message'))


if __name__ == '__main__':
    unittest.main()
