"""
Unit and Integration Tests for the My Complaints Module in AJIT ZONE PVT. LTD. Complaint Management System.

Covers:
- RBACService ownership mapping: is_created_by_user, is_handled_by_user, is_my_complaint
- Endpoint GET /api/complaints/my and GET /api/complaints?scope=my
- Scope filtering: my, assigned, created
- Filter criteria: department, status, priority, full-text search
- From Department filter: Online, Offline, Packing, None (single and multi-selection)
- Combined filters: From Dept + Status + Priority + Department + Search
- Pagination: page, limit, total_records, total_pages
- Role: Complaint Executive (Nita Chaudhary / ajitzonealleditor@gmail.com)
- Cross-department security isolation
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


class TestMyComplaintsModule(unittest.TestCase):

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

        # Seed custom role Complaint Executive
        self.custom_roles = [
            {
                'Role ID': 'ROLE-007',
                'Name': 'Complaint Executive',
                'Description': 'Executive handling company complaints',
                'Data Scope': 'DEPARTMENT',
                'Permissions': [
                    'complaints:view', 'complaints:create', 'complaints:edit',
                    'complaints:assign', 'complaints:change_status', 'complaints:close',
                    'complaints:reopen', 'comments:view', 'comments:add', 'comments:add_internal',
                    'attachments:view', 'attachments:upload', 'attachments:delete',
                    'reports:view', 'reports:create', 'reports:save', 'reports:export_excel',
                    'reports:export_csv', 'reports:export_pdf', 'reports:print',
                    'audit_logs:view', 'audit_logs:export'
                ],
                'Is System Role': 'FALSE'
            }
        ]
        MockSheetsDB._store['Roles'].append({
            'Role ID': 'ROLE-007',
            'Name': 'Complaint Executive',
            'Description': 'Executive handling company complaints',
            'Data Scope': 'DEPARTMENT',
            'Permissions': 'complaints:view,complaints:create,complaints:edit,complaints:assign,complaints:change_status,complaints:close,complaints:reopen',
            'Is System Role': 'FALSE'
        })

        # Test users
        self.executive_user = {
            'user_id': 'USR-0003',
            'name': 'Nita Chaudhary',
            'email': 'ajitzonealleditor@gmail.com',
            'role': 'Complaint Executive',
            'department': 'Online, Dress Material, Readymade, Top, Kurti, Lehenga, Gown, Saree, Kids, Undergarments, Nighty, Dupatta, Bottom, Complaint',
            'is_active': 'TRUE'
        }

        self.staff_user = {
            'user_id': 'USR-0004',
            'name': 'Pooja Patel',
            'email': 'pooja@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Kurti',
            'is_active': 'TRUE'
        }

        self.regular_user = {
            'user_id': 'USR-0005',
            'name': 'Ramesh Kumar',
            'email': 'ramesh@customer.com',
            'role': 'User',
            'department': '',
            'is_active': 'TRUE'
        }

    # =========================================================================
    # 1. UNIT TESTS: RBACService Ownership Mapping
    # =========================================================================

    def test_is_created_by_user_by_email(self):
        complaint = {
            'Complaint ID': 'CMP-101',
            'Complainant Email': 'ajitzonealleditor@gmail.com',
            'Employee Name': 'Other Staff'
        }
        self.assertTrue(RBACService.is_created_by_user(self.executive_user, complaint))

    def test_is_created_by_user_by_employee_name_token(self):
        # 'NITA' token should match 'Nita Chaudhary'
        complaint = {
            'Complaint ID': 'CMP-102',
            'Complainant Email': 'hr@example.com',
            'Employee Name': 'NITA'
        }
        self.assertTrue(RBACService.is_created_by_user(self.executive_user, complaint))

    def test_is_created_by_user_by_user_id(self):
        complaint = {
            'Complaint ID': 'CMP-103',
            'Complainant Email': 'unknown@example.com',
            'User ID': 'USR-0003'
        }
        self.assertTrue(RBACService.is_created_by_user(self.executive_user, complaint))

    def test_is_handled_by_user_by_name_token(self):
        # 'NITA' matches 'Nita Chaudhary'
        complaint = {
            'Complaint ID': 'CMP-104',
            'Handled By': 'NITA'
        }
        self.assertTrue(RBACService.is_handled_by_user(self.executive_user, complaint))

    def test_is_handled_by_user_by_email(self):
        complaint = {
            'Complaint ID': 'CMP-105',
            'Handled By': 'ajitzonealleditor@gmail.com'
        }
        self.assertTrue(RBACService.is_handled_by_user(self.executive_user, complaint))

    def test_is_handled_by_user_by_assigned_to_name(self):
        complaint = {
            'Complaint ID': 'CMP-106',
            'Handled By': 'Pending',
            'Assigned To Name': 'Nita Chaudhary'
        }
        self.assertTrue(RBACService.is_handled_by_user(self.executive_user, complaint))

    def test_is_my_complaint_logic(self):
        # Handled but not created
        c_handled = {
            'Complaint ID': 'CMP-107',
            'Complainant Email': 'other@ex.com',
            'Handled By': 'Nita Chaudhary'
        }
        self.assertTrue(RBACService.is_my_complaint(self.executive_user, c_handled))

        # Created but not handled
        c_created = {
            'Complaint ID': 'CMP-108',
            'Complainant Email': 'ajitzonealleditor@gmail.com',
            'Handled By': 'Vikram'
        }
        self.assertTrue(RBACService.is_my_complaint(self.executive_user, c_created))

        # Neither created nor handled
        c_other = {
            'Complaint ID': 'CMP-109',
            'Complainant Email': 'stranger@ex.com',
            'Employee Name': 'Vikram',
            'Handled By': 'Pooja Patel'
        }
        self.assertFalse(RBACService.is_my_complaint(self.executive_user, c_other))

    # =========================================================================
    # 2. INTEGRATION TESTS: GET /api/complaints/my and Query Filters
    # =========================================================================

    def _seed_test_complaints(self):
        """Seeds a diverse set of test complaints in MockSheetsDB."""
        complaints = [
            # C-01: Assigned to Nita, Created by Nita, Kurti dept, High prio, Under Review, From Department: Online
            {
                'Complaint ID': 'CMP-TEST-01',
                'Customer Name': 'Anjali Gupta',
                'Complainant Email': 'ajitzonealleditor@gmail.com',
                'Customer Mobile No.': '9876500001',
                'Invoice No.': 'INV-001',
                'Complain Types': 'Defective Stitching',
                'Department Name': 'Kurti',
                'From Department': 'Online',
                'Handled By': 'NITA',
                'Employee Name': 'NITA',
                'Priority': 'High',
                'Status': 'Under Review',
                'Remarks': 'Needs urgent replacement',
                'Timestamp': '2026-11-19 10:00:00'
            },
            # C-02: Assigned to Nita, Created by Ramesh (User), Saree dept, Medium prio, In Progress, From Department: Offline
            {
                'Complaint ID': 'CMP-TEST-02',
                'Customer Name': 'Sneha Rao',
                'Complainant Email': 'ramesh@customer.com',
                'Customer Mobile No.': '9876500002',
                'Invoice No.': 'INV-002',
                'Complain Types': 'Color Bleeding',
                'Department Name': 'Saree',
                'From Department': 'Offline',
                'Handled By': 'Nita Chaudhary',
                'Employee Name': 'Sales Staff',
                'Priority': 'Medium',
                'Status': 'In Progress',
                'Remarks': 'Fabric sent to lab',
                'Timestamp': '2026-11-19 11:00:00'
            },
            # C-03: Created by Nita, Handled by Pooja, Dress Material dept, Low prio, Closed, From Department: Packing
            {
                'Complaint ID': 'CMP-TEST-03',
                'Customer Name': 'Deepak Sharma',
                'Complainant Email': 'ajitzonealleditor@gmail.com',
                'Customer Mobile No.': '9876500003',
                'Invoice No.': 'INV-003',
                'Complain Types': 'Missing Dupatta',
                'Department Name': 'Dress Material',
                'From Department': 'Packing',
                'Handled By': 'Pooja Patel',
                'Employee Name': 'Nita Chaudhary',
                'Priority': 'Low',
                'Status': 'Closed',
                'Remarks': 'Replacement dispatched',
                'Timestamp': '2026-11-19 12:00:00'
            },
            # C-04: Neither assigned nor created by Nita (Handled by Pooja, Created by Ramesh)
            {
                'Complaint ID': 'CMP-TEST-04',
                'Customer Name': 'Vikram Mehta',
                'Complainant Email': 'ramesh@customer.com',
                'Customer Mobile No.': '9876500004',
                'Invoice No.': 'INV-004',
                'Complain Types': 'Wrong Size',
                'Department Name': 'Kurti',
                'From Department': 'Offline',
                'Handled By': 'Pooja Patel',
                'Employee Name': 'Sales Staff',
                'Priority': 'Low',
                'Status': 'Submitted',
                'Remarks': 'Awaiting customer response',
                'Timestamp': '2026-11-19 13:00:00'
            },
            # C-05: Dress Material dept, Handled by Suresh, Created by Ramesh (completely unrelated)
            {
                'Complaint ID': 'CMP-TEST-05',
                'Customer Name': 'Meena Devi',
                'Complainant Email': 'meena@test.com',
                'Customer Mobile No.': '9876500005',
                'Invoice No.': 'INV-005',
                'Complain Types': 'Defective Zipper',
                'Department Name': 'Dress Material',
                'From Department': 'Online',
                'Handled By': 'Suresh',
                'Employee Name': 'Counter',
                'Priority': 'Urgent',
                'Status': 'Resolved',
                'Remarks': 'Repaired',
                'Timestamp': '2026-11-19 14:00:00'
            },
            # C-06: Assigned to Nita, Created by Ramesh, Kurti dept, Urgent prio, Submitted, From Department: None
            {
                'Complaint ID': 'CMP-TEST-06',
                'Customer Name': 'Kavita Singh',
                'Complainant Email': 'ramesh@customer.com',
                'Customer Mobile No.': '9876500006',
                'Invoice No.': 'INV-006',
                'Complain Types': 'Fabric Defect',
                'Department Name': 'Kurti',
                'From Department': 'None',
                'Handled By': 'NITA',
                'Employee Name': 'Counter Staff',
                'Priority': 'Urgent',
                'Status': 'Submitted',
                'Remarks': 'Needs supervisor check',
                'Timestamp': '2026-11-19 15:00:00'
            }
        ]
        MockSheetsDB._store['Complaints'].extend(complaints)

    def test_api_my_complaints_endpoint_returns_only_my_records(self):
        self._seed_test_complaints()

        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        # GET /api/complaints/my
        res = self.client.get('/api/complaints/my')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')
        complaints = data.get('complaints', [])

        # Executive Nita should see C-01, C-02, C-03, C-06 (handled or created)
        # Should NOT see C-04 or C-05 in My Complaints
        comp_ids = [c['Complaint ID'] for c in complaints]
        self.assertIn('CMP-TEST-01', comp_ids)
        self.assertIn('CMP-TEST-02', comp_ids)
        self.assertIn('CMP-TEST-03', comp_ids)
        self.assertIn('CMP-TEST-06', comp_ids)
        self.assertNotIn('CMP-TEST-04', comp_ids)
        self.assertNotIn('CMP-TEST-05', comp_ids)
        self.assertEqual(len(complaints), 4)

    def test_api_complaints_scope_query_param(self):
        self._seed_test_complaints()

        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        # 1. GET /api/complaints?scope=my
        res_my = self.client.get('/api/complaints?scope=my')
        self.assertEqual(res_my.status_code, 200)
        data_my = res_my.get_json()
        ids_my = [c['Complaint ID'] for c in data_my['complaints']]
        self.assertEqual(set(ids_my), {'CMP-TEST-01', 'CMP-TEST-02', 'CMP-TEST-03', 'CMP-TEST-06'})

        # 2. GET /api/complaints?scope=assigned (Handled by Nita: C-01, C-02, C-06)
        res_assigned = self.client.get('/api/complaints?scope=assigned')
        self.assertEqual(res_assigned.status_code, 200)
        data_assigned = res_assigned.get_json()
        ids_assigned = [c['Complaint ID'] for c in data_assigned['complaints']]
        self.assertEqual(set(ids_assigned), {'CMP-TEST-01', 'CMP-TEST-02', 'CMP-TEST-06'})

        # 3. GET /api/complaints?scope=created (Created by Nita: C-01, C-03)
        res_created = self.client.get('/api/complaints?scope=created')
        self.assertEqual(res_created.status_code, 200)
        data_created = res_created.get_json()
        ids_created = [c['Complaint ID'] for c in data_created['complaints']]
        self.assertEqual(set(ids_created), {'CMP-TEST-01', 'CMP-TEST-03'})

    # =========================================================================
    # 3. FROM DEPARTMENT FILTER TESTS (Online, Offline, Packing, None)
    # =========================================================================

    def test_from_department_single_filter_online(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        res = self.client.get('/api/complaints/my?from_dept=Online')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        complaints = data.get('complaints', [])
        self.assertEqual(len(complaints), 1)
        self.assertEqual(complaints[0]['Complaint ID'], 'CMP-TEST-01')
        self.assertEqual(complaints[0]['From Department'], 'Online')

    def test_from_department_single_filter_offline(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        res = self.client.get('/api/complaints/my?from_dept=Offline')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        complaints = data.get('complaints', [])
        self.assertEqual(len(complaints), 1)
        self.assertEqual(complaints[0]['Complaint ID'], 'CMP-TEST-02')
        self.assertEqual(complaints[0]['From Department'], 'Offline')

    def test_from_department_single_filter_packing(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        res = self.client.get('/api/complaints/my?from_dept=Packing')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        complaints = data.get('complaints', [])
        self.assertEqual(len(complaints), 1)
        self.assertEqual(complaints[0]['Complaint ID'], 'CMP-TEST-03')
        self.assertEqual(complaints[0]['From Department'], 'Packing')

    def test_from_department_single_filter_none(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        res = self.client.get('/api/complaints/my?from_dept=None')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        complaints = data.get('complaints', [])
        self.assertEqual(len(complaints), 1)
        self.assertEqual(complaints[0]['Complaint ID'], 'CMP-TEST-06')
        self.assertEqual(complaints[0]['From Department'], 'None')

    def test_from_department_multiple_selection(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        # Multi-select: Online and Offline
        res = self.client.get('/api/complaints/my?from_dept=Online,Offline')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        ids = [c['Complaint ID'] for c in data.get('complaints', [])]
        self.assertEqual(set(ids), {'CMP-TEST-01', 'CMP-TEST-02'})

        # Multi-select: Packing and None
        res_p_n = self.client.get('/api/complaints/my?from_dept=Packing,None')
        self.assertEqual(res_p_n.status_code, 200)
        ids_p_n = [c['Complaint ID'] for c in res_p_n.get_json().get('complaints', [])]
        self.assertEqual(set(ids_p_n), {'CMP-TEST-03', 'CMP-TEST-06'})

    def test_from_department_combined_filters(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        # Combined: From Dept=Online, Dept=Kurti, Priority=High, Status=Under Review, Search=Stitching
        res = self.client.get('/api/complaints/my?from_dept=Online&department=Kurti&priority=High&status=Under%20Review&search=Stitching')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data['complaints']), 1)
        self.assertEqual(data['complaints'][0]['Complaint ID'], 'CMP-TEST-01')

        # Filter mismatch: From Dept=Online but Status=Closed -> should return 0 results
        res_mismatch = self.client.get('/api/complaints/my?from_dept=Online&status=Closed')
        self.assertEqual(res_mismatch.status_code, 200)
        self.assertEqual(len(res_mismatch.get_json()['complaints']), 0)

    def test_from_department_clear_all_resets_filter(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        # from_dept=ALL or omitted
        res_all = self.client.get('/api/complaints/my?from_dept=ALL')
        self.assertEqual(res_all.status_code, 200)
        self.assertEqual(len(res_all.get_json()['complaints']), 4)

    def test_from_department_preserves_rbac_cross_department_security(self):
        self._seed_test_complaints()
        with self.client.session_transaction() as sess:
            sess['user'] = self.staff_user  # Pooja Patel, only Kurti department

        # Pooja queries from_dept=Online (which has C-01 Kurti and C-05 Dress Material)
        # C-05 belongs to Dress Material which Pooja is NOT authorized for and did not create/handle
        res = self.client.get('/api/complaints?from_dept=Online')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        ids = [c['Complaint ID'] for c in data.get('complaints', [])]
        self.assertIn('CMP-TEST-01', ids)  # Kurti matches
        self.assertNotIn('CMP-TEST-05', ids)  # Dress Material unauthorized

    def test_api_my_complaints_pagination(self):
        self._seed_test_complaints()

        with self.client.session_transaction() as sess:
            sess['user'] = self.executive_user

        # Page 1, limit 2
        res_p1 = self.client.get('/api/complaints/my?page=1&limit=2')
        self.assertEqual(res_p1.status_code, 200)
        data_p1 = res_p1.get_json()
        self.assertEqual(data_p1.get('status'), 'success')
        self.assertEqual(data_p1.get('count'), 2)
        self.assertEqual(data_p1.get('total_records'), 4)
        self.assertIn('pagination', data_p1)
        self.assertEqual(data_p1['pagination']['page'], 1)
        self.assertEqual(data_p1['pagination']['limit'], 2)
        self.assertEqual(data_p1['pagination']['total_pages'], 2)

        # Page 2, limit 2
        res_p2 = self.client.get('/api/complaints/my?page=2&limit=2')
        self.assertEqual(res_p2.status_code, 200)
        data_p2 = res_p2.get_json()
        self.assertEqual(data_p2.get('count'), 2)
        self.assertEqual(data_p2['pagination']['page'], 2)


if __name__ == '__main__':
    unittest.main()
