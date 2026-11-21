"""
Unit & Integration Tests for Reports & Analytics Filters:
- Department Filter (single & multi-selection, comma-separated tags, Department Name field)
- Vendor Name Filter (single & multi-selection, case-insensitive, Vendor Name field)
- From Department Filter (Online, Offline, Packing, None)
- Combined multi-criteria filtering
- Dynamic Aggregations, KPIs, and Charts accuracy
- Full /api/reports endpoint integration (GET & POST)
- RBAC departmental security enforcement
"""

import os
import sys
import unittest
import json
import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app
from config import Config
from services.mock_sheets_db import MockSheetsDB
from services.rbac_service import RBACService
from services.report_service import ReportService


class TestReportsFilters(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        Config.INITIAL_ADMIN_PASSWORD = 'Admin@123456'

        cls.client = app.test_client()
        cls.db = MockSheetsDB()
        app.config['DB_INSTANCE'] = cls.db

        # Seed master departments
        MockSheetsDB._store['Departments'] = [
            {'Department ID': 'DEP-001', 'Name': 'Accounts', 'Status': 'Active'},
            {'Department ID': 'DEP-002', 'Name': 'Warehouse', 'Status': 'Active'},
            {'Department ID': 'DEP-003', 'Name': 'Packing', 'Status': 'Active'},
            {'Department ID': 'DEP-004', 'Name': 'Sales', 'Status': 'Active'},
            {'Department ID': 'DEP-005', 'Name': 'Customer Support', 'Status': 'Active'}
        ]

        # Seed test complaints covering diverse departments, vendors, and from_departments
        cls.test_complaints = [
            {
                'Complaint ID': 'REP-001',
                'Complain Date': '2026-10-15',
                'Customer Name': 'Amit Sharma',
                'Customer Mobile No.': '9876543210',
                'Department Name': 'Accounts',
                'From Department': 'Online',
                'Vendor Name': 'Textile Mills Ltd',
                'Status': 'Resolved',
                'Priority': 'High',
                'Handled By': 'Staff Executive 1',
                'GR Amount': '1500.00',
                'Complain Types': 'Billing Error',
                'Category': 'Finance'
            },
            {
                'Complaint ID': 'REP-002',
                'Complain Date': '2026-10-20',
                'Customer Name': 'Bhavna Patel',
                'Customer Mobile No.': '9876543211',
                'Department Name': 'Accounts, Warehouse',
                'From Department': 'Offline',
                'Vendor Name': 'Textile Mills Ltd',
                'Status': 'In Progress',
                'Priority': 'Medium',
                'Handled By': 'Staff Executive 2',
                'GR Amount': '2500.50',
                'Complain Types': 'Damaged Goods',
                'Category': 'Logistics'
            },
            {
                'Complaint ID': 'REP-003',
                'Complain Date': '2026-11-05',
                'Customer Name': 'Chirag Desai',
                'Customer Mobile No.': '9876543212',
                'Department Name': 'Warehouse',
                'From Department': 'Packing',
                'Vendor Name': 'Apex Logistics',
                'Status': 'Resolved',
                'Priority': 'Low',
                'Handled By': 'Staff Executive 1',
                'GR Amount': '800.00',
                'Complain Types': 'Late Dispatch',
                'Category': 'Operations'
            },
            {
                'Complaint ID': 'REP-004',
                'Complain Date': '2026-11-12',
                'Customer Name': 'Divya Shah',
                'Customer Mobile No.': '9876543213',
                'Department Name': 'Packing',
                'From Department': 'None',
                'Vendor Name': 'Apex Logistics',
                'Status': 'Submitted',
                'Priority': 'Urgent',
                'Handled By': 'Staff Executive 3',
                'GR Amount': '4200.00',
                'Complain Types': 'Defective Stitching',
                'Category': 'Quality'
            },
            {
                'Complaint ID': 'REP-005',
                'Complain Date': '2026-11-18',
                'Customer Name': 'Esha Gupta',
                'Customer Mobile No.': '9876543214',
                'Department Name': 'Sales',
                'From Department': '',  # Empty From Department implies 'None'
                'Vendor Name': 'Zenith Garments',
                'Status': 'Under Review',
                'Priority': 'High',
                'Handled By': 'Staff Executive 2',
                'GR Amount': '0.00',
                'Complain Types': 'Wrong Item Received',
                'Category': 'Order Processing'
            },
            {
                'Complaint ID': 'REP-006',
                'Complain Date': '2026-11-20',
                'Customer Name': 'Farhan Khan',
                'Customer Mobile No.': '9876543215',
                'Department Name': 'Packing, Sales',
                'From Department': 'Online',
                'Vendor Name': 'Zenith Garments',
                'Status': 'Resolved',
                'Priority': 'Medium',
                'Handled By': 'Staff Executive 1',
                'GR Amount': '1250.00',
                'Complain Types': 'Color Mismatch',
                'Category': 'Quality'
            }
        ]

        MockSheetsDB._store['Complaints'] = [dict(c) for c in cls.test_complaints]

    def setUp(self):
        MockSheetsDB._store['Complaints'] = [dict(c) for c in self.test_complaints]

    # =========================================================================
    # 1. DEPARTMENT FILTER TESTS
    # =========================================================================

    def test_department_filter_single_selection(self):
        """Test single department selection matching Department Name field."""
        # 'Accounts' should match REP-001 ('Accounts') and REP-002 ('Accounts, Warehouse')
        filtered = ReportService.apply_filters(self.test_complaints, {'departments': ['Accounts']})
        self.assertEqual(len(filtered), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-001', 'REP-002'})

    def test_department_filter_multi_selection(self):
        """Test multiple department selections (OR logic within departments)."""
        # 'Accounts' or 'Sales' should match REP-001, REP-002, REP-005, REP-006
        filtered = ReportService.apply_filters(self.test_complaints, {'departments': ['Accounts', 'Sales']})
        self.assertEqual(len(filtered), 4)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-001', 'REP-002', 'REP-005', 'REP-006'})

    def test_department_filter_comma_separated_string(self):
        """Test department filter passed as comma-separated string parameter."""
        filtered = ReportService.apply_filters(self.test_complaints, {'departments': 'Warehouse, Packing'})
        # REP-002 ('Accounts, Warehouse'), REP-003 ('Warehouse'), REP-004 ('Packing'), REP-006 ('Packing, Sales')
        self.assertEqual(len(filtered), 4)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-002', 'REP-003', 'REP-004', 'REP-006'})

    def test_department_filter_case_insensitivity(self):
        """Test that department filtering is strictly case-insensitive."""
        filtered_lower = ReportService.apply_filters(self.test_complaints, {'departments': ['accounts']})
        filtered_upper = ReportService.apply_filters(self.test_complaints, {'departments': ['ACCOUNTS']})
        self.assertEqual(len(filtered_lower), 2)
        self.assertEqual(len(filtered_upper), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered_lower}, {'REP-001', 'REP-002'})

    # =========================================================================
    # 2. VENDOR NAME FILTER TESTS
    # =========================================================================

    def test_vendor_filter_single_selection(self):
        """Test single vendor selection matching Vendor Name field."""
        # 'Textile Mills Ltd' matches REP-001 and REP-002
        filtered = ReportService.apply_filters(self.test_complaints, {'vendors': ['Textile Mills Ltd']})
        self.assertEqual(len(filtered), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-001', 'REP-002'})

    def test_vendor_filter_multi_selection(self):
        """Test multiple vendors selection (OR logic within vendors)."""
        # 'Textile Mills Ltd' or 'Zenith Garments' matches REP-001, REP-002, REP-005, REP-006
        filtered = ReportService.apply_filters(self.test_complaints, {'vendors': ['Textile Mills Ltd', 'Zenith Garments']})
        self.assertEqual(len(filtered), 4)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-001', 'REP-002', 'REP-005', 'REP-006'})

    def test_vendor_filter_case_insensitivity(self):
        """Test that vendor filtering is case-insensitive."""
        filtered = ReportService.apply_filters(self.test_complaints, {'vendors': ['apex logistics']})
        self.assertEqual(len(filtered), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-003', 'REP-004'})

    # =========================================================================
    # 3. FROM DEPARTMENT FILTER TESTS (Online, Offline, Packing, None)
    # =========================================================================

    def test_from_department_filter_online(self):
        """Test From Department filter with 'Online'."""
        filtered = ReportService.apply_filters(self.test_complaints, {'from_department': ['Online']})
        self.assertEqual(len(filtered), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-001', 'REP-006'})

    def test_from_department_filter_offline(self):
        """Test From Department filter with 'Offline'."""
        filtered = ReportService.apply_filters(self.test_complaints, {'from_department': ['Offline']})
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['Complaint ID'], 'REP-002')

    def test_from_department_filter_packing(self):
        """Test From Department filter with 'Packing'."""
        filtered = ReportService.apply_filters(self.test_complaints, {'from_department': ['Packing']})
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['Complaint ID'], 'REP-003')

    def test_from_department_filter_none(self):
        """Test From Department filter with 'None' (covers explicit 'None' and empty string)."""
        filtered = ReportService.apply_filters(self.test_complaints, {'from_department': ['None']})
        # REP-004 has 'None', REP-005 has '' -> both normalize to 'none'
        self.assertEqual(len(filtered), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-004', 'REP-005'})

    def test_from_department_filter_multiple_choices(self):
        """Test From Department multi-select (e.g. Online + None)."""
        filtered = ReportService.apply_filters(self.test_complaints, {'from_department': ['Online', 'None']})
        # Online: REP-001, REP-006; None: REP-004, REP-005
        self.assertEqual(len(filtered), 4)
        self.assertEqual({c['Complaint ID'] for c in filtered}, {'REP-001', 'REP-004', 'REP-005', 'REP-006'})

    def test_from_department_all_choices_selected(self):
        """Selecting all four from-department options should include all records."""
        filtered = ReportService.apply_filters(
            self.test_complaints,
            {'from_department': ['Online', 'Offline', 'Packing', 'None']}
        )
        self.assertEqual(len(filtered), 6)

    # =========================================================================
    # 4. COMBINED FILTERING & CLEAR FILTERS
    # =========================================================================

    def test_combined_filters_department_vendor_from_dept(self):
        """Test combining Department, Vendor, and From Department simultaneously."""
        # Department: 'Accounts' (REP-001, REP-002)
        # Vendor: 'Textile Mills Ltd' (REP-001, REP-002)
        # From Dept: 'Online' (REP-001, REP-006)
        # Intersection: only REP-001
        filtered = ReportService.apply_filters(self.test_complaints, {
            'departments': ['Accounts'],
            'vendors': ['Textile Mills Ltd'],
            'from_department': ['Online']
        })
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['Complaint ID'], 'REP-001')

    def test_combined_filters_with_status_and_priority(self):
        """Test combining Department, From Department, Status, and Priority."""
        filtered = ReportService.apply_filters(self.test_complaints, {
            'departments': ['Packing', 'Sales'],
            'from_department': ['Online'],
            'statuses': ['Resolved'],
            'priorities': ['Medium']
        })
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['Complaint ID'], 'REP-006')

    def test_clear_filters_returns_all_records(self):
        """Empty or cleared filter dictionary returns all records intact."""
        filtered = ReportService.apply_filters(self.test_complaints, {})
        self.assertEqual(len(filtered), 6)

    # =========================================================================
    # 5. AGGREGATIONS, KPI CARDS & CHARTS ACCURACY
    # =========================================================================

    def test_aggregations_match_filtered_subset_strictly(self):
        """
        Verify that aggregations, KPI cards, and chart data strictly match
        the filtered records, NOT the whole database.
        """
        # Filter to only Online complaints (REP-001 and REP-006)
        # REP-001: GR Amount 1500.00, Status Resolved, Dept Accounts, Vendor Textile Mills Ltd
        # REP-006: GR Amount 1250.00, Status Resolved, Dept Packing, Sales, Vendor Zenith Garments
        filtered = ReportService.apply_filters(self.test_complaints, {'from_department': ['Online']})
        aggregations = ReportService.calculate_aggregations(filtered)

        kpis = aggregations['kpis']
        self.assertEqual(kpis['total_complaints'], 2)
        self.assertEqual(kpis['resolved_count'], 2)
        self.assertEqual(kpis['pending_count'], 0)
        self.assertEqual(kpis['resolution_rate'], 100.0)
        self.assertEqual(kpis['total_gr_amount'], 2750.00)
        self.assertEqual(kpis['pending_gr_amount'], 0.0)

        # Department breakdown must only reflect REP-001 and REP-006
        dept_map = {d['department']: d['count'] for d in aggregations['department_breakdown']}
        self.assertEqual(dept_map.get('Accounts'), 1)
        self.assertEqual(dept_map.get('Packing'), 1)
        self.assertEqual(dept_map.get('Sales'), 1)
        self.assertNotIn('Warehouse', dept_map)

        # Top vendors must only reflect the 2 filtered vendors
        vendor_map = {v['vendor']: v['count'] for v in aggregations['top_vendors']}
        self.assertEqual(vendor_map.get('Textile Mills Ltd'), 1)
        self.assertEqual(vendor_map.get('Zenith Garments'), 1)
        self.assertNotIn('Apex Logistics', vendor_map)

    # =========================================================================
    # 6. /API/REPORTS ENDPOINT INTEGRATION (GET & POST)
    # =========================================================================

    def _login_super_admin(self):
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'id': 'ADM-001',
                'email': 'superadmin@ajitzone.com',
                'name': 'Super Admin',
                'role': 'Super Admin',
                'departments': ['General', 'Accounts', 'Warehouse', 'Packing', 'Sales'],
                'permissions': ['reports:view', 'reports:export_excel', 'reports:export_csv', 'reports:export_pdf']
            }

    def test_api_reports_get_with_filters_and_master_options(self):
        """Test GET /api/reports returns filtered records and available options."""
        self._login_super_admin()
        res = self.client.get('/api/reports?departments=Accounts&from_department=Online')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data['status'], 'success')

        # Record count
        self.assertEqual(data['pagination']['total_records'], 1)
        self.assertEqual(data['records'][0]['Complaint ID'], 'REP-001')

        # Dropdown options returned from Google Sheets database
        self.assertIn('available_departments', data)
        self.assertIn('Accounts', data['available_departments'])
        self.assertIn('Warehouse', data['available_departments'])

        self.assertIn('available_vendors', data)
        self.assertIn('Textile Mills Ltd', data['available_vendors'])
        self.assertIn('Zenith Garments', data['available_vendors'])

        self.assertIn('available_from_departments', data)
        self.assertIn('Online', data['available_from_departments'])
        self.assertIn('Offline', data['available_from_departments'])
        self.assertIn('Packing', data['available_from_departments'])
        self.assertIn('None', data['available_from_departments'])

    def test_api_reports_post_with_multi_select_arrays(self):
        """Test POST /api/reports with JSON array filters."""
        self._login_super_admin()
        payload = {
            'departments': ['Accounts', 'Packing'],
            'vendors': ['Textile Mills Ltd', 'Apex Logistics'],
            'from_department': ['Online', 'Packing']
        }
        res = self.client.post(
            '/api/reports',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data['status'], 'success')
        # REP-001: Accounts, Textile Mills Ltd, Online -> matches
        # REP-003: Warehouse, Apex Logistics, Packing -> Dept doesn't match
        # REP-004: Packing, Apex Logistics, None -> From Dept doesn't match
        # Match should be REP-001
        self.assertEqual(data['pagination']['total_records'], 1)
        self.assertEqual(data['records'][0]['Complaint ID'], 'REP-001')

    # =========================================================================
    # 7. RBAC DEPARTMENT SECURITY SCOPING
    # =========================================================================

    def test_rbac_department_staff_restricted_reports(self):
        """
        Verify that Support Staff assigned to 'Accounts' department can only
        see complaints belonging to 'Accounts' department in Reports.
        """
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'id': 'STF-001',
                'email': 'staff.accounts@ajitzone.com',
                'name': 'Staff Accounts',
                'role': 'Support Staff',
                'departments': ['Accounts'],
                'permissions': ['reports:view']
            }

        res = self.client.get('/api/reports')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)

        # Accounts complaints in test data: REP-001 ('Accounts') and REP-002 ('Accounts, Warehouse')
        self.assertEqual(data['pagination']['total_records'], 2)
        rec_ids = {r['Complaint ID'] for r in data['records']}
        self.assertEqual(rec_ids, {'REP-001', 'REP-002'})

        # Cross-department complaints must NEVER appear
        self.assertNotIn('REP-003', rec_ids)
        self.assertNotIn('REP-004', rec_ids)
        self.assertNotIn('REP-005', rec_ids)


if __name__ == '__main__':
    unittest.main()
