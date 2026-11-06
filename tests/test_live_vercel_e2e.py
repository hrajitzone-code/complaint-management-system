"""
Strictly Read-Only Production Verification Suite for Complaint Management System
Runs against the deployed Vercel production URL.
Zero Write / Zero Dummy Data Policy Compliant:
- NEVER creates, modifies, or deletes complaints.
- NEVER creates, modifies, or deletes users.
- NEVER modifies any Google Sheets records or Google Drive files.
"""
import os
import sys
import json
import requests
import unittest

BASE_URL = os.environ.get("VERCEL_TEST_URL", "https://complaint-management-system-sigma-nine.vercel.app").rstrip("/")

class TestVercelProductionReadOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = requests.Session()
        cls.base_url = BASE_URL
        print(f"\n[READ-ONLY VERIFICATION] Suite running against: {cls.base_url}")

    def test_01_homepage_renders(self):
        """Verify frontend single-page application loads with HTTP 200."""
        resp = self.session.get(f"{self.base_url}/")
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}")
        self.assertIn("Complaint Management System", resp.text)
        print("  [PASS] 01: Homepage and UI assets loaded successfully (HTTP 200).")

    def test_02_system_status_and_db(self):
        """Verify system status reports Google Sheets API v4 as only database engine."""
        resp = self.session.get(f"{self.base_url}/api/system/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("database_engine"), "Google Sheets API v4")
        self.assertEqual(data.get("active_spreadsheet_id"), "1Zp-8bCef5eEjA2F_fMiduNfF-evzxCe0K1dTn8Kp23Y")
        
        drive = data.get("drive", {})
        self.assertEqual(drive.get("auth_mode"), "user_oauth")
        self.assertEqual(drive.get("folder_name"), "My work")
        self.assertTrue(drive.get("has_folder_access"))
        self.assertTrue(drive.get("can_upload_binary"))
        print(f"  [PASS] 02: Google Sheets API v4 verified. Drive folder '{drive.get('folder_name')}' accessible.")

    def test_03_admin_authentication(self):
        """Verify authentication via POST /api/auth/login with existing credentials."""
        login_payload = {
            "email": "admin@complaints.local",
            "password": "Admin@123456"
        }
        resp = self.session.post(f"{self.base_url}/api/auth/login", json=login_payload)
        self.assertEqual(resp.status_code, 200, f"Login failed: {resp.text}")
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("user", data)
        self.assertEqual(data["user"].get("role"), "Super Admin")

        # Verify /api/auth/me session cookie
        me_resp = self.session.get(f"{self.base_url}/api/auth/me")
        self.assertEqual(me_resp.status_code, 200)
        me_data = me_resp.json()
        self.assertEqual(me_data.get("user", {}).get("email"), "admin@complaints.local")
        print(f"  [PASS] 03: Authenticated as Super Admin ({data['user'].get('email')}). Session verified.")

    def test_04_department_management_read_only(self):
        """Verify department statistics and listing (read-only)."""
        resp = self.session.get(f"{self.base_url}/api/departments")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIsInstance(data.get("departments"), list)
        self.assertGreater(len(data.get("departments")), 0)
        dept_names = [d.get("Department Name") or d.get("Name") or d.get("name") or str(d) for d in data.get("departments")]
        print(f"  [PASS] 04: Retrieved {len(data.get('departments'))} existing departments: {', '.join(dept_names[:4])}...")

    def test_05_read_complaints_zero_writes(self):
        """Verify complaints listing without performing any create/update/delete operations."""
        resp = self.session.get(f"{self.base_url}/api/complaints")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("complaints", data)
        total = data.get("total", len(data.get("complaints", [])))
        print(f"  [PASS] 05: Read-only complaints listing verified. Current total complaints: {total}.")

    def test_06_reports_dynamic_filtering_read_only(self):
        """Verify Reports & Analytics query without writing data."""
        report_payload = {
            "date_range": "all",
            "departments": [],
            "statuses": [],
            "priorities": []
        }
        resp = self.session.post(f"{self.base_url}/api/reports", json=report_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("kpis", data)
        self.assertIn("records", data)
        print(f"  [PASS] 06: Reports & Analytics query verified (read-only).")

    def test_07_export_csv_read_only(self):
        """Verify CSV export (read-only query)."""
        export_payload = {
            "date_range": "all",
            "columns": ["complaint_id", "invoice_no", "customer_name", "status", "priority", "created_at"]
        }
        resp = self.session.post(f"{self.base_url}/api/reports/export/csv", json=export_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers.get("Content-Type", ""))
        self.assertIn("Complaint ID", resp.text)
        print(f"  [PASS] 07: CSV export verified ({len(resp.content)} bytes, read-only).")

    def test_08_export_excel_read_only(self):
        """Verify Excel (.xlsx) export (read-only query)."""
        export_payload = {
            "date_range": "all",
            "columns": ["complaint_id", "invoice_no", "customer_name", "status", "priority", "created_at"]
        }
        resp = self.session.post(f"{self.base_url}/api/reports/export/excel", json=export_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.headers.get("Content-Type", "").lower())
        self.assertTrue(resp.content.startswith(b"PK\x03\x04"), "Invalid XLSX ZIP header")
        print(f"  [PASS] 08: Excel (.xlsx) export verified ({len(resp.content)} bytes, read-only).")

    def test_09_export_pdf_read_only(self):
        """Verify PDF export (read-only query)."""
        export_payload = {
            "date_range": "all",
            "columns": ["complaint_id", "invoice_no", "customer_name", "status", "priority"]
        }
        resp = self.session.post(f"{self.base_url}/api/reports/export/pdf", json=export_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/pdf", resp.headers.get("Content-Type", ""))
        self.assertTrue(resp.content.startswith(b"%PDF"), "Invalid PDF header")
        print(f"  [PASS] 09: PDF export verified ({len(resp.content)} bytes, read-only).")

    def test_10_drive_status_read_only(self):
        """Verify Google Drive status and permissions without writing any test file."""
        resp = self.session.get(f"{self.base_url}/api/drive/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        drive = data.get("drive", {})
        self.assertTrue(drive.get("has_folder_access"))
        self.assertTrue(drive.get("can_upload_binary"))
        print(f"  [PASS] 10: Google Drive status verified (read-only). Active account: {drive.get('active_account_email')}.")

if __name__ == "__main__":
    unittest.main()
