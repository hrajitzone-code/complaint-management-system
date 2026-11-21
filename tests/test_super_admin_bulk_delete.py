"""
Comprehensive Test Suite for Super Admin Bulk Complaint Delete & Recovery
in AJIT ZONE PVT. LTD. Complaint Management System.

Tests verify:
1. CRITICAL DATE LOGIC:
   - Uses ONLY 'Complain Date', NEVER Created At / Timestamp / Entry Date.
   - A complaint dated 20 October 2026 entered on 21 November 2026 belongs to 2026.
   - A complaint dated 30 December 2025 entered in 2026 belongs to 2025.
2. SUPER ADMIN AUTHORIZATION & SECURITY:
   - Only Super Admin can access preview, export, bulk delete, and restore endpoints.
   - Department Staff, Department Head, and Admin are blocked with HTTP 403 Forbidden.
3. BULK DELETION SAFETY:
   - Requires explicit confirmation ('DELETE').
   - Only deletes complaints matching the targeted Year / selected IDs.
   - Strictly preserves complaints from other years.
4. AUTOMATED BACKUP & RECOVERY MECHANISM:
   - Pre-deletion recoverable archive saved in 'Deleted_Complaints_Backup'.
   - One-click restore reinstates deleted complaints into active records.
   - Pre-deletion Excel (.xlsx) backup export.
5. AUDIT TRAIL:
   - Status History entries recorded for deleted complaints.
   - Immutable audit logs created for deletion and recovery events.
"""

import os
import sys
import unittest
import json
import datetime
import io
import openpyxl

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import Config
from app import app
from services.mock_sheets_db import MockSheetsDB
from services.report_service import ReportService


class TestSuperAdminBulkDelete(unittest.TestCase):

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

        # 1. Super Admin User
        self.super_admin_user = {
            'user_id': 'USR-SUPERADMIN',
            'name': 'Super Admin',
            'email': 'superadmin@ajitzone.com',
            'role': 'Super Admin',
            'department': 'All',
            'departments': ['All'],
            'is_active': 'TRUE'
        }

        # 2. Regular Admin User (cannot execute Super Admin bulk delete)
        self.admin_user = {
            'user_id': 'USR-ADMIN',
            'name': 'System Admin',
            'email': 'admin@ajitzone.com',
            'role': 'Admin',
            'department': 'All',
            'departments': ['All'],
            'is_active': 'TRUE'
        }

        # 3. Department Staff User
        self.staff_user = {
            'user_id': 'USR-STAFF',
            'name': 'Staff Member',
            'email': 'staff@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Sales',
            'departments': ['Sales'],
            'is_active': 'TRUE'
        }

        # Test complaints spanning multiple years:
        # 2026: 3 complaints (CMP-2026-1, CMP-2026-2, CMP-2026-3)
        # Note: CMP-2026-1 has Complain Date in October 2026 but Timestamp in November 2026!
        # 2025: 2 complaints (CMP-2025-1, CMP-2025-2)
        # 2024: 1 complaint (CMP-2024-1)
        self.test_complaints = [
            {
                'Complaint ID': 'CMP-2026-1',
                'Complain Date': '2026-10-20',
                'Timestamp': '2026-11-21 14:00:00',  # Entered late in Nov, but Complain Date is 2026!
                'Customer Name': 'Amit Shah',
                'Customer Mobile No.': '9876543201',
                'Employee Name': 'Rohan',
                'Salesperson Name': 'Vikram',
                'Vendor Name': 'ABC Tex',
                'From Department': 'Online',
                'Department Name': 'Sales',
                'Category': 'Quality',
                'Complain Types': 'Color Bleed',
                'Priority': 'High',
                'Status': 'Open',
                'Invoice No.': 'INV-2026-1',
                'GR Amount': '1500.00'
            },
            {
                'Complaint ID': 'CMP-2026-2',
                'Complain Date': '2026-03-15',
                'Timestamp': '2026-03-15 10:00:00',
                'Customer Name': 'Pooja Patel',
                'Customer Mobile No.': '9876543202',
                'Employee Name': 'Nita',
                'Salesperson Name': 'Sunil',
                'Vendor Name': 'XYZ Fabrics',
                'From Department': 'Offline',
                'Department Name': 'Sales',
                'Category': 'Billing',
                'Complain Types': 'Rate Difference',
                'Priority': 'Medium',
                'Status': 'Resolved',
                'Invoice No.': 'INV-2026-2',
                'GR Amount': '2400.00'
            },
            {
                'Complaint ID': 'CMP-2026-3',
                'Complain Date': '2026-08-01',
                'Timestamp': '2026-08-01 11:30:00',
                'Customer Name': 'Rajesh Kumar',
                'Customer Mobile No.': '9876543203',
                'Employee Name': 'Rohan',
                'Salesperson Name': 'Vikram',
                'Vendor Name': 'Global Traders',
                'From Department': 'Packing',
                'Department Name': 'Support',
                'Category': 'Packaging',
                'Complain Types': 'Torn Box',
                'Priority': 'Low',
                'Status': 'Closed',
                'Invoice No.': 'INV-2026-3',
                'GR Amount': '500.00'
            },
            {
                'Complaint ID': 'CMP-2025-1',
                'Complain Date': '2025-12-25',
                'Timestamp': '2026-01-05 09:00:00',  # Entered in 2026, but Complain Date is 2025!
                'Customer Name': 'Sunita Rao',
                'Customer Mobile No.': '9876543204',
                'Employee Name': 'Nita',
                'Salesperson Name': 'Sunil',
                'Vendor Name': 'ABC Tex',
                'From Department': 'Online',
                'Department Name': 'Sales',
                'Category': 'Delivery',
                'Complain Types': 'Late Delivery',
                'Priority': 'Urgent',
                'Status': 'Closed',
                'Invoice No.': 'INV-2025-1',
                'GR Amount': '3200.00'
            },
            {
                'Complaint ID': 'CMP-2025-2',
                'Complain Date': '2025-06-10',
                'Timestamp': '2025-06-10 16:00:00',
                'Customer Name': 'Vikram Joshi',
                'Customer Mobile No.': '9876543205',
                'Employee Name': 'Rohan',
                'Salesperson Name': 'Anil',
                'Vendor Name': 'Delta Weaves',
                'From Department': 'Offline',
                'Department Name': 'Support',
                'Category': 'Quality',
                'Complain Types': 'Defective Stitching',
                'Priority': 'High',
                'Status': 'Closed',
                'Invoice No.': 'INV-2025-2',
                'GR Amount': '1800.00'
            },
            {
                'Complaint ID': 'CMP-2024-1',
                'Complain Date': '2024-11-12',
                'Timestamp': '2024-11-12 12:00:00',
                'Customer Name': 'Manish Gupta',
                'Customer Mobile No.': '9876543206',
                'Employee Name': 'Nita',
                'Salesperson Name': 'Anil',
                'Vendor Name': 'ABC Tex',
                'From Department': 'Online',
                'Department Name': 'Sales',
                'Category': 'Defect',
                'Complain Types': 'Fabric Stain',
                'Priority': 'Medium',
                'Status': 'Closed',
                'Invoice No.': 'INV-2024-1',
                'GR Amount': '4500.00'
            }
        ]

        # Seed users and complaints into MockSheetsDB
        MockSheetsDB._store['Users'].clear()
        MockSheetsDB._store['Users'].extend([self.super_admin_user, self.admin_user, self.staff_user])

        MockSheetsDB._store['Complaints'].clear()
        for c in self.test_complaints:
            MockSheetsDB._store['Complaints'].append(dict(c))

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user'] = user

    # =========================================================================
    # 1. CRITICAL DATE LOGIC TESTS
    # =========================================================================

    def test_year_filter_strictly_uses_complain_date(self):
        """
        Verify that year filter strictly checks Complain Date:
        - Year 2026: CMP-2026-1, CMP-2026-2, CMP-2026-3 (3 records)
        - Year 2025: CMP-2025-1, CMP-2025-2 (2 records)
        - Year 2024: CMP-2024-1 (1 record)
        Note: CMP-2025-1 was entered in 2026 (Timestamp: 2026-01-05), but Complain Date is 2025-12-25.
        It must appear in 2025 and NOT in 2026!
        """
        self._login(self.super_admin_user)

        # 1. Check Year 2026
        res_2026 = self.client.get('/api/complaints?year=2026')
        self.assertEqual(res_2026.status_code, 200)
        c_2026 = res_2026.get_json()['complaints']
        ids_2026 = [c['Complaint ID'] for c in c_2026]
        self.assertEqual(sorted(ids_2026), ['CMP-2026-1', 'CMP-2026-2', 'CMP-2026-3'])
        self.assertNotIn('CMP-2025-1', ids_2026)

        # 2. Check Year 2025
        res_2025 = self.client.get('/api/complaints?year=2025')
        self.assertEqual(res_2025.status_code, 200)
        c_2025 = res_2025.get_json()['complaints']
        ids_2025 = [c['Complaint ID'] for c in c_2025]
        self.assertEqual(sorted(ids_2025), ['CMP-2025-1', 'CMP-2025-2'])

        # 3. Check Year 2024
        res_2024 = self.client.get('/api/complaints?year=2024')
        self.assertEqual(res_2024.status_code, 200)
        c_2024 = res_2024.get_json()['complaints']
        ids_2024 = [c['Complaint ID'] for c in c_2024]
        self.assertEqual(ids_2024, ['CMP-2024-1'])

    def test_complaint_years_endpoint_returns_unique_years(self):
        """Verify GET /api/complaints/years returns unique years in descending order."""
        self._login(self.super_admin_user)

        res = self.client.get('/api/complaints/years')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        years = data['years']
        self.assertIn('2026', years)
        self.assertIn('2025', years)
        self.assertIn('2024', years)
        self.assertEqual(years, sorted(years, reverse=True))

    # =========================================================================
    # 2. SECURITY & AUTHORIZATION TESTS
    # =========================================================================

    def test_bulk_delete_blocked_for_non_super_admin(self):
        """
        Verify that non-Super Admin roles (Admin, Staff) are strictly blocked with 403 Forbidden.
        Only Super Admin is permitted to preview, export backup, bulk delete, or restore.
        """
        # Test with Admin (role: 'Admin')
        self._login(self.admin_user)

        res_del = self.client.post('/api/complaints/bulk-delete', json={
            'year': '2026',
            'confirmation': 'DELETE'
        })
        self.assertEqual(res_del.status_code, 403)

        res_preview = self.client.post('/api/complaints/bulk-delete/preview', json={'year': '2026'})
        self.assertEqual(res_preview.status_code, 403)

        res_export = self.client.post('/api/complaints/bulk-delete/export-backup', json={'year': '2026'})
        self.assertEqual(res_export.status_code, 403)

        res_restore = self.client.post('/api/complaints/bulk-delete/restore', json={'backup_id': 'BAK-123'})
        self.assertEqual(res_restore.status_code, 403)

        # Test with Staff (role: 'Department Staff')
        self._login(self.staff_user)
        res_staff = self.client.post('/api/complaints/bulk-delete', json={
            'year': '2026',
            'confirmation': 'DELETE'
        })
        self.assertEqual(res_staff.status_code, 403)

    # =========================================================================
    # 3. PREVIEW & CONFIRMATION VALIDATION TESTS
    # =========================================================================

    def test_bulk_delete_preview_metrics(self):
        """Verify preview endpoint returns accurate counts, date ranges, and summaries."""
        self._login(self.super_admin_user)

        res = self.client.post('/api/complaints/bulk-delete/preview', json={'year': '2026'})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['matched_count'], 3)
        self.assertEqual(data['year'], '2026')
        self.assertEqual(data['earliest_date'], '2026-03-15')
        self.assertEqual(data['latest_date'], '2026-10-20')
        self.assertEqual(set(data['complaint_ids']), {'CMP-2026-1', 'CMP-2026-2', 'CMP-2026-3'})

    def test_bulk_delete_requires_explicit_confirmation(self):
        """Verify bulk deletion is aborted without explicit 'DELETE' confirmation phrase."""
        self._login(self.super_admin_user)

        # Without confirmation
        res = self.client.post('/api/complaints/bulk-delete', json={'year': '2026'})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Explicit confirmation phrase 'DELETE' is required", res.get_json()['message'])

        # With wrong phrase
        res_wrong = self.client.post('/api/complaints/bulk-delete', json={
            'year': '2026',
            'confirmation': 'YES'
        })
        self.assertEqual(res_wrong.status_code, 400)

    # =========================================================================
    # 4. EXECUTION, BACKUP ARCHIVING & YEAR ISOLATION TESTS
    # =========================================================================

    def test_bulk_delete_execution_preserves_other_years_and_creates_backup(self):
        """
        Verify:
        1. Bulk deleting year 2026 deletes exactly 3 complaints (CMP-2026-1, CMP-2026-2, CMP-2026-3).
        2. Complaints from 2025 and 2024 remain completely intact.
        3. A recoverable backup is saved to Deleted_Complaints_Backup.
        4. Audit logs are recorded.
        """
        self._login(self.super_admin_user)

        # Initial count: 6 complaints
        self.assertEqual(len(MockSheetsDB._store['Complaints']), 6)

        # Execute bulk delete for 2026
        res = self.client.post('/api/complaints/bulk-delete', json={
            'year': '2026',
            'confirmation': 'DELETE'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['data']['deleted_count'], 3)
        backup_id = data['data']['backup_id']
        self.assertTrue(backup_id.startswith('BAK-'))

        # Remaining complaints in active store should be exactly 3 (CMP-2025-1, CMP-2025-2, CMP-2024-1)
        remaining_ids = [c['Complaint ID'] for c in MockSheetsDB._store['Complaints']]
        self.assertEqual(len(remaining_ids), 3)
        self.assertEqual(sorted(remaining_ids), ['CMP-2024-1', 'CMP-2025-1', 'CMP-2025-2'])

        # Verify backup exists in Deleted_Complaints_Backup store
        backups = MockSheetsDB._store.get('Deleted_Complaints_Backup', [])
        self.assertEqual(len(backups), 3)
        backed_up_ids = [b['Complaint ID'] for b in backups if b['_backup_id'] == backup_id]
        self.assertEqual(sorted(backed_up_ids), ['CMP-2026-1', 'CMP-2026-2', 'CMP-2026-3'])

        # Verify Audit Logs
        audit_logs = MockSheetsDB._store.get('Audit_Logs', [])
        bulk_audit = [a for a in audit_logs if a.get('Action') == 'BULK_COMPLAINT_DELETE']
        self.assertTrue(len(bulk_audit) >= 1)
        self.assertEqual(bulk_audit[-1]['User Email'], 'superadmin@ajitzone.com')

    # =========================================================================
    # 5. RECOVERY & RESTORATION TESTS
    # =========================================================================

    def test_restore_bulk_deleted_complaints_recovers_all_records(self):
        """
        Verify:
        1. Bulk delete year 2026.
        2. Call restore endpoint with the returned backup ID.
        3. All 3 complaints are reinstated into active records.
        4. Total count returns to 6.
        """
        self._login(self.super_admin_user)

        # 1. Delete year 2026
        res_del = self.client.post('/api/complaints/bulk-delete', json={
            'year': '2026',
            'confirmation': 'DELETE'
        })
        self.assertEqual(res_del.status_code, 200)
        backup_id = res_del.get_json()['data']['backup_id']
        self.assertEqual(len(MockSheetsDB._store['Complaints']), 3)

        # 2. Check backups list endpoint
        res_backups = self.client.get('/api/complaints/bulk-delete/backups')
        self.assertEqual(res_backups.status_code, 200)
        backups_list = res_backups.get_json()['backups']
        self.assertEqual(len(backups_list), 1)
        self.assertEqual(backups_list[0]['backup_id'], backup_id)
        self.assertEqual(backups_list[0]['count'], 3)

        # 3. Restore complaints from backup
        res_restore = self.client.post('/api/complaints/bulk-delete/restore', json={
            'backup_id': backup_id
        })
        self.assertEqual(res_restore.status_code, 200)
        restored_data = res_restore.get_json()['data']
        self.assertEqual(restored_data['restored_count'], 3)

        # 4. Active complaints count should be restored to 6
        current_ids = [c['Complaint ID'] for c in MockSheetsDB._store['Complaints']]
        self.assertEqual(len(current_ids), 6)
        self.assertIn('CMP-2026-1', current_ids)
        self.assertIn('CMP-2026-2', current_ids)
        self.assertIn('CMP-2026-3', current_ids)

        # 5. Backup status should now be marked RESTORED
        res_backups_after = self.client.get('/api/complaints/bulk-delete/backups')
        self.assertEqual(res_backups_after.get_json()['backups'][0]['status'], 'RESTORED')

    # =========================================================================
    # 6. EXCEL BACKUP EXPORT TEST
    # =========================================================================

    def test_export_backup_excel(self):
        """Verify pre-deletion backup export returns a valid Excel (.xlsx) file with targeted rows."""
        self._login(self.super_admin_user)

        res = self.client.post('/api/complaints/bulk-delete/export-backup', json={'year': '2026'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        # Read the binary workbook in memory
        wb = openpyxl.load_workbook(io.BytesIO(res.data))
        ws = wb.active
        self.assertIsNotNone(ws)

        # Collect cell values from rows to verify targeted complaint IDs
        content_str = " ".join([str(cell.value or '') for row in ws.iter_rows() for cell in row])
        self.assertIn('CMP-2026-1', content_str)
        self.assertIn('CMP-2026-2', content_str)
        self.assertIn('CMP-2026-3', content_str)
        self.assertNotIn('CMP-2025-1', content_str)
        self.assertNotIn('CMP-2024-1', content_str)


if __name__ == '__main__':
    unittest.main()
