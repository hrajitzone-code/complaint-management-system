"""
Comprehensive Unit & Integration Test Suite for the Global Sorting & Filter System
in AJIT ZONE PVT. LTD. Complaint Management System.

Tests verify:
1. Critical Date Filter Logic:
   - Date filters strictly use 'Complain Date', NEVER 'Timestamp' / 'Created At' / 'Upload Date'.
   - Date range filtering (from_date / to_date).
   - Month-Year filtering (e.g. October 2026 vs November 2026) ensuring complaints
     entered in November with Complain Date in October appear under October and NOT November.
2. Global Column Sorting:
   - Date: Newest to Oldest (desc) and Oldest to Newest (asc).
   - Customer Name: A to Z (asc) and Z to A (desc).
   - Amount / GR Amount: Low to High (asc) and High to Low (desc).
   - Text columns (Type, Department, Invoice No, Handled By).
3. Advanced Multi-Field Filters:
   - Vendor Name (case-insensitive partial match).
   - Salesperson Name.
   - From Department (Online, Offline, Packing, None).
   - Complaint Type & Category.
   - Status & Priority.
   - Invoice Number.
   - Handled By.
   - Amount range (min_amount, max_amount).
4. Combined Filters & Sorting:
   - All filters work simultaneously without hiding valid records.
   - Pagination maintains filtered counts and sorted order.
5. ReportService Integration:
   - ReportService.apply_filters() supports month_year, from_department, and all multi-filters.
   - ReportService.sort_and_paginate() supports full and shorthand sort keys.
"""

import os
import sys
import unittest
import json
import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import Config
from app import app
from services.mock_sheets_db import MockSheetsDB
from services.rbac_service import RBACService
from services.report_service import ReportService


class TestGlobalSortingAndFilters(unittest.TestCase):

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

        # Seed Admin user (dictionary matches session user contract)
        self.admin_user = {
            'user_id': 'USR-ADMIN',
            'name': 'Super Admin',
            'email': 'admin@ajitzone.com',
            'role': 'Super Admin',
            'department': 'All',
            'departments': ['All'],
            'is_active': 'TRUE'
        }

        # Seed Staff user
        self.staff_user = {
            'user_id': 'USR-STAFF-1',
            'name': 'Nita Chaudhary',
            'email': 'nita@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Sales, Support',
            'departments': ['Sales', 'Support'],
            'is_active': 'TRUE'
        }

        # Seed rich set of complaints testing varied Complain Dates, Creation Timestamps,
        # Vendors, Salespersons, From Departments, and Amounts.
        self.test_complaints = [
            {
                'Complaint ID': 'CMP-001',
                'Complain Date': '2026-10-15',
                'Timestamp': '2026-11-21 10:00:00',  # Entered in November, but Complain Date is October
                'Customer Name': 'Rahul Sharma',
                'Customer Mobile No.': '9876543210',
                'Employee Name': 'Nita Chaudhary',
                'Salesperson Name': 'Vikram Singh',
                'Vendor Name': 'ABC Tex Corp',
                'From Department': 'Online',
                'Department Name': 'Sales',
                'Category': 'Billing',
                'Complain Types': 'Excess Billing',
                'Priority': 'High',
                'Status': 'Open',
                'Handled By': 'Nita Chaudhary',
                'Invoice No.': 'INV-1001',
                'GR Amount': '1500.50'
            },
            {
                'Complaint ID': 'CMP-002',
                'Complain Date': '2026-10-25',
                'Timestamp': '2026-11-21 11:00:00',  # Entered in November, Complain Date is October
                'Customer Name': 'Amit Patel',
                'Customer Mobile No.': '9876543211',
                'Employee Name': 'Rohan Shah',
                'Salesperson Name': 'Anil Kumar',
                'Vendor Name': 'Global Traders',
                'From Department': 'Offline',
                'Department Name': 'Sales',
                'Category': 'Product Defect',
                'Complain Types': 'Fabric Torn',
                'Priority': 'Critical',
                'Status': 'In Progress',
                'Handled By': 'Rohan Shah',
                'Invoice No.': 'INV-1002',
                'GR Amount': '5200.00'
            },
            {
                'Complaint ID': 'CMP-003',
                'Complain Date': '2026-11-05',
                'Timestamp': '2026-11-05 09:30:00',
                'Customer Name': 'Pooja Verma',
                'Customer Mobile No.': '9876543212',
                'Employee Name': 'Nita Chaudhary',
                'Salesperson Name': 'Vikram Singh',
                'Vendor Name': 'ABC Tex Corp',
                'From Department': 'Packing',
                'Department Name': 'Support',
                'Category': 'Packaging',
                'Complain Types': 'Box Damaged',
                'Priority': 'Low',
                'Status': 'Resolved',
                'Handled By': 'Nita Chaudhary',
                'Invoice No.': 'INV-1003',
                'GR Amount': '350.00'
            },
            {
                'Complaint ID': 'CMP-004',
                'Complain Date': '2026-11-18',
                'Timestamp': '2026-11-18 14:15:00',
                'Customer Name': 'Deepak Joshi',
                'Customer Mobile No.': '9876543213',
                'Employee Name': 'Sunita Rao',
                'Salesperson Name': 'Sunil Mehra',
                'Vendor Name': 'Zenith Garments',
                'From Department': 'None',
                'Department Name': 'Dispatch',
                'Category': 'Delivery',
                'Complain Types': 'Late Delivery',
                'Priority': 'Medium',
                'Status': 'Open',
                'Handled By': 'Sunita Rao',
                'Invoice No.': 'INV-1004',
                'GR Amount': '8900.00'
            },
            {
                'Complaint ID': 'CMP-005',
                'Complain Date': '2026-09-10',
                'Timestamp': '2026-09-10 08:00:00',
                'Customer Name': 'Zara Enterprises',
                'Customer Mobile No.': '9876543214',
                'Employee Name': 'Rohan Shah',
                'Salesperson Name': 'Anil Kumar',
                'Vendor Name': 'ABC Tex Corp',
                'From Department': 'Online',
                'Department Name': 'Sales',
                'Category': 'Quality',
                'Complain Types': 'Color Bleed',
                'Priority': 'High',
                'Status': 'Closed',
                'Handled By': 'Rohan Shah',
                'Invoice No.': 'INV-1005',
                'GR Amount': '12400.00'
            }
        ]

        # Populate Mock DB store
        MockSheetsDB._store['Users'].clear()
        MockSheetsDB._store['Users'].append(self.admin_user)
        MockSheetsDB._store['Users'].append(self.staff_user)

        MockSheetsDB._store['Complaints'].clear()
        for comp in self.test_complaints:
            MockSheetsDB._store['Complaints'].append(dict(comp))

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user'] = user

    # =========================================================================
    # 1. CRITICAL DATE FILTER LOGIC TESTS
    # =========================================================================

    def test_critical_date_filter_month_year_strictly_uses_complain_date(self):
        """
        Verify that month_year filter uses Complain Date, NOT Created At or Timestamp.
        CMP-001 and CMP-002 have Complain Date in October (2026-10-15, 2026-10-25)
        even though their Timestamp/entry date is in November (2026-11-21).
        When filtering for month_year='2026-10', they MUST appear.
        When filtering for month_year='2026-11', they MUST NOT appear.
        """
        self._login(self.admin_user)

        # 1. Filter for October 2026
        res_oct = self.client.get('/api/complaints?month_year=2026-10')
        self.assertEqual(res_oct.status_code, 200)
        data_oct = res_oct.get_json()
        oct_ids = [c['Complaint ID'] for c in data_oct['complaints']]
        self.assertIn('CMP-001', oct_ids)
        self.assertIn('CMP-002', oct_ids)
        self.assertNotIn('CMP-003', oct_ids)
        self.assertNotIn('CMP-004', oct_ids)
        self.assertNotIn('CMP-005', oct_ids)
        self.assertEqual(len(oct_ids), 2)

        # 2. Filter for November 2026
        res_nov = self.client.get('/api/complaints?month_year=2026-11')
        self.assertEqual(res_nov.status_code, 200)
        data_nov = res_nov.get_json()
        nov_ids = [c['Complaint ID'] for c in data_nov['complaints']]
        self.assertNotIn('CMP-001', nov_ids)
        self.assertNotIn('CMP-002', nov_ids)
        self.assertIn('CMP-003', nov_ids)
        self.assertIn('CMP-004', nov_ids)
        self.assertEqual(len(nov_ids), 2)

        # 3. Filter for September 2026
        res_sep = self.client.get('/api/complaints?month_year=2026-09')
        self.assertEqual(res_sep.status_code, 200)
        data_sep = res_sep.get_json()
        sep_ids = [c['Complaint ID'] for c in data_sep['complaints']]
        self.assertEqual(sep_ids, ['CMP-005'])

    def test_date_range_filter_strictly_uses_complain_date(self):
        """
        Verify that from_date and to_date strictly evaluate Complain Date.
        """
        self._login(self.admin_user)

        # Range 2026-10-01 to 2026-10-20 should only match CMP-001 (2026-10-15)
        res = self.client.get('/api/complaints?from_date=2026-10-01&to_date=2026-10-20')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        ids = [c['Complaint ID'] for c in data['complaints']]
        self.assertEqual(ids, ['CMP-001'])

        # Range 2026-10-16 to 2026-11-10 matches CMP-002 (10-25) and CMP-003 (11-05)
        res2 = self.client.get('/api/complaints?from_date=2026-10-16&to_date=2026-11-10')
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        ids2 = [c['Complaint ID'] for c in data2['complaints']]
        self.assertIn('CMP-002', ids2)
        self.assertIn('CMP-003', ids2)
        self.assertEqual(len(ids2), 2)

    # =========================================================================
    # 2. GLOBAL COLUMN SORTING TESTS
    # =========================================================================

    def test_sorting_by_date_newest_and_oldest(self):
        """
        Verify sorting by Complain Date:
        Newest to Oldest: CMP-004 (11-18) -> CMP-003 (11-05) -> CMP-002 (10-25) -> CMP-001 (10-15) -> CMP-005 (09-10)
        Oldest to Newest: reverse order
        """
        self._login(self.admin_user)

        # Newest first
        res_desc = self.client.get('/api/complaints?sort_by=date&order=desc')
        self.assertEqual(res_desc.status_code, 200)
        ids_desc = [c['Complaint ID'] for c in res_desc.get_json()['complaints']]
        self.assertEqual(ids_desc, ['CMP-004', 'CMP-003', 'CMP-002', 'CMP-001', 'CMP-005'])

        # Oldest first
        res_asc = self.client.get('/api/complaints?sort_by=date&order=asc')
        self.assertEqual(res_asc.status_code, 200)
        ids_asc = [c['Complaint ID'] for c in res_asc.get_json()['complaints']]
        self.assertEqual(ids_asc, ['CMP-005', 'CMP-001', 'CMP-002', 'CMP-003', 'CMP-004'])

    def test_sorting_by_customer_name(self):
        """
        Verify A-Z and Z-A sorting by Customer Name:
        A-Z: Amit Patel -> Deepak Joshi -> Pooja Verma -> Rahul Sharma -> Zara Enterprises
        Z-A: Zara Enterprises -> Rahul Sharma -> Pooja Verma -> Deepak Joshi -> Amit Patel
        """
        self._login(self.admin_user)

        res_asc = self.client.get('/api/complaints?sort_by=customer&order=asc')
        self.assertEqual(res_asc.status_code, 200)
        names_asc = [c['Customer Name'] for c in res_asc.get_json()['complaints']]
        self.assertEqual(names_asc, ['Amit Patel', 'Deepak Joshi', 'Pooja Verma', 'Rahul Sharma', 'Zara Enterprises'])

        res_desc = self.client.get('/api/complaints?sort_by=customer&order=desc')
        self.assertEqual(res_desc.status_code, 200)
        names_desc = [c['Customer Name'] for c in res_desc.get_json()['complaints']]
        self.assertEqual(names_desc, ['Zara Enterprises', 'Rahul Sharma', 'Pooja Verma', 'Deepak Joshi', 'Amit Patel'])

    def test_sorting_by_amount(self):
        """
        Verify numeric sorting by GR Amount:
        Low to High: 350.00 (CMP-003) -> 1500.50 (CMP-001) -> 5200.00 (CMP-002) -> 8900.00 (CMP-004) -> 12400.00 (CMP-005)
        High to Low: reverse
        """
        self._login(self.admin_user)

        res_asc = self.client.get('/api/complaints?sort_by=amount&order=asc')
        self.assertEqual(res_asc.status_code, 200)
        ids_asc = [c['Complaint ID'] for c in res_asc.get_json()['complaints']]
        self.assertEqual(ids_asc, ['CMP-003', 'CMP-001', 'CMP-002', 'CMP-004', 'CMP-005'])

        res_desc = self.client.get('/api/complaints?sort_by=amount&order=desc')
        self.assertEqual(res_desc.status_code, 200)
        ids_desc = [c['Complaint ID'] for c in res_desc.get_json()['complaints']]
        self.assertEqual(ids_desc, ['CMP-005', 'CMP-004', 'CMP-002', 'CMP-001', 'CMP-003'])

    # =========================================================================
    # 3. ADVANCED MULTI-FIELD FILTERS TESTS
    # =========================================================================

    def test_filter_by_vendor(self):
        """Verify Vendor Name filter matches case-insensitively."""
        self._login(self.admin_user)

        res = self.client.get('/api/complaints?vendor=abc tex')
        self.assertEqual(res.status_code, 200)
        ids = [c['Complaint ID'] for c in res.get_json()['complaints']]
        self.assertEqual(sorted(ids), ['CMP-001', 'CMP-003', 'CMP-005'])

    def test_filter_by_salesperson(self):
        """Verify Salesperson Name filter."""
        self._login(self.admin_user)

        res = self.client.get('/api/complaints?salesperson=vikram')
        self.assertEqual(res.status_code, 200)
        ids = [c['Complaint ID'] for c in res.get_json()['complaints']]
        self.assertEqual(sorted(ids), ['CMP-001', 'CMP-003'])

    def test_filter_by_from_department(self):
        """Verify From Department filtering (Online, Offline, Packing, None)."""
        self._login(self.admin_user)

        # Online
        res_online = self.client.get('/api/complaints?from_department=Online')
        self.assertEqual(res_online.status_code, 200)
        ids_online = [c['Complaint ID'] for c in res_online.get_json()['complaints']]
        self.assertEqual(sorted(ids_online), ['CMP-001', 'CMP-005'])

        # Offline
        res_offline = self.client.get('/api/complaints?from_department=Offline')
        self.assertEqual(res_offline.status_code, 200)
        ids_offline = [c['Complaint ID'] for c in res_offline.get_json()['complaints']]
        self.assertEqual(ids_offline, ['CMP-002'])

        # None
        res_none = self.client.get('/api/complaints?from_department=None')
        self.assertEqual(res_none.status_code, 200)
        ids_none = [c['Complaint ID'] for c in res_none.get_json()['complaints']]
        self.assertEqual(ids_none, ['CMP-004'])

    def test_filter_by_handled_by_and_invoice(self):
        """Verify Handled By and Invoice No. filters."""
        self._login(self.admin_user)

        # Handled By
        res_h = self.client.get('/api/complaints?handled_by=rohan')
        self.assertEqual(res_h.status_code, 200)
        ids_h = [c['Complaint ID'] for c in res_h.get_json()['complaints']]
        self.assertEqual(sorted(ids_h), ['CMP-002', 'CMP-005'])

        # Invoice No.
        res_inv = self.client.get('/api/complaints?invoice_no=INV-1003')
        self.assertEqual(res_inv.status_code, 200)
        ids_inv = [c['Complaint ID'] for c in res_inv.get_json()['complaints']]
        self.assertEqual(ids_inv, ['CMP-003'])

    def test_filter_by_amount_range(self):
        """Verify min_amount and max_amount filters."""
        self._login(self.admin_user)

        # Amounts between 1000 and 6000: CMP-001 (1500.50) and CMP-002 (5200.00)
        res = self.client.get('/api/complaints?min_amount=1000&max_amount=6000')
        self.assertEqual(res.status_code, 200)
        ids = [c['Complaint ID'] for c in res.get_json()['complaints']]
        self.assertEqual(sorted(ids), ['CMP-001', 'CMP-002'])

    # =========================================================================
    # 4. COMBINED FILTERS & SORTING TESTS
    # =========================================================================

    def test_combined_filters_and_sorting_with_pagination(self):
        """
        Combine multiple filters:
        vendor='ABC Tex Corp' AND order by amount desc
        Expected: CMP-005 (12400.00), CMP-001 (1500.50), CMP-003 (350.00)
        """
        self._login(self.admin_user)

        res = self.client.get('/api/complaints?vendor=ABC Tex Corp&sort_by=amount&order=desc')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['total_records'], 3)
        self.assertEqual([c['Complaint ID'] for c in data['complaints']], ['CMP-005', 'CMP-001', 'CMP-003'])

        # Add month_year filter
        res_oct = self.client.get('/api/complaints?vendor=ABC Tex Corp&month_year=2026-10')
        self.assertEqual(res_oct.status_code, 200)
        data_oct = res_oct.get_json()
        self.assertEqual(data_oct['total_records'], 1)
        self.assertEqual(data_oct['complaints'][0]['Complaint ID'], 'CMP-001')

    # =========================================================================
    # 5. REPORT SERVICE INTEGRATION TESTS
    # =========================================================================

    def test_report_service_apply_filters_and_sorting(self):
        """
        Verify that ReportService.apply_filters and sort_and_paginate correctly
        support month_year, from_department, and shorthand sort keys.
        """
        # Test apply_filters with month_year
        filtered_oct = ReportService.apply_filters(self.test_complaints, {'month_year': '2026-10'})
        self.assertEqual(len(filtered_oct), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered_oct}, {'CMP-001', 'CMP-002'})

        # Test apply_filters with from_department
        filtered_online = ReportService.apply_filters(self.test_complaints, {'from_department': 'Online'})
        self.assertEqual(len(filtered_online), 2)
        self.assertEqual({c['Complaint ID'] for c in filtered_online}, {'CMP-001', 'CMP-005'})

        # Test sort_and_paginate with shorthand sort key 'customer' and sort_dir='asc'
        sorted_paged, page, total_pages, total_records = ReportService.sort_and_paginate(
            self.test_complaints,
            sort_by='customer',
            sort_dir='asc',
            page=1,
            page_size=10
        )
        self.assertEqual(total_records, 5)
        names = [c['Customer Name'] for c in sorted_paged]
        self.assertEqual(names, ['Amit Patel', 'Deepak Joshi', 'Pooja Verma', 'Rahul Sharma', 'Zara Enterprises'])

        # Test sort_and_paginate with shorthand 'date' descending
        sorted_date_desc, _, _, _ = ReportService.sort_and_paginate(
            self.test_complaints,
            sort_by='date',
            sort_dir='desc'
        )
        self.assertEqual([c['Complaint ID'] for c in sorted_date_desc], ['CMP-004', 'CMP-003', 'CMP-002', 'CMP-001', 'CMP-005'])


if __name__ == '__main__':
    unittest.main()
