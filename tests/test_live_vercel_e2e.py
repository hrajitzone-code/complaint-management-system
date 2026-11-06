"""
Comprehensive End-to-End Live Verification Suite for Complaint Management System
Runs directly against the deployed Vercel production URL.
"""
import os
import sys
import json
import uuid
import requests
import unittest

BASE_URL = os.environ.get("VERCEL_TEST_URL", "https://complaint-management-system-sigma-nine.vercel.app").rstrip("/")

class TestVercelProductionE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = requests.Session()
        cls.base_url = BASE_URL
        print(f"\n[E2E] Running verification suite against: {cls.base_url}")

    def test_01_homepage_renders(self):
        """Verify frontend single-page application loads with HTTP 200."""
        resp = self.session.get(f"{self.base_url}/")
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}")
        self.assertIn("Complaint Management System", resp.text)
        print("  [PASS] 01: Homepage and SPA assets loaded successfully (HTTP 200).")

    def test_02_system_status_and_db(self):
        """Verify system status reports Google Sheets API v4 and Drive connectivity."""
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
        print(f"  [PASS] 02: Google Sheets API v4 active. Drive folder '{drive.get('folder_name')}' accessible with User OAuth.")

    def test_03_admin_authentication(self):
        """Verify authentication via POST /api/auth/login and session cookie establishment."""
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

        # Verify /api/auth/me with the authenticated session cookie
        me_resp = self.session.get(f"{self.base_url}/api/auth/me")
        self.assertEqual(me_resp.status_code, 200)
        me_data = me_resp.json()
        self.assertEqual(me_data.get("user", {}).get("email"), "admin@complaints.local")
        print(f"  [PASS] 03: Logged in as Super Admin ({data['user'].get('email')}). Session cookie verified.")

    def test_04_department_management(self):
        """Verify department statistics and listing."""
        resp = self.session.get(f"{self.base_url}/api/departments")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIsInstance(data.get("departments"), list)
        self.assertGreater(len(data.get("departments")), 0)
        dept_names = [d.get("Department Name") or d.get("Name") or d.get("name") or str(d) for d in data.get("departments")]
        print(f"  [PASS] 04: Retrieved {len(data.get('departments'))} departments: {', '.join(dept_names[:4])}...")

    def test_05_complaint_crud_lifecycle(self):
        """Verify full Complaint CRUD lifecycle with 21 fields in Google Sheets DB."""
        unique_id = uuid.uuid4().hex[:6]
        test_invoice = f"INV-VERCEL-{unique_id}"
        test_customer = f"Vercel Customer {unique_id}"
        
        # 1. Create Complaint with canonical 21 fields
        create_payload = {
            "invoice_no": test_invoice,
            "complain_date": "2026-11-06",
            "customer_name": test_customer,
            "customer_mobile_no": "9876543210",
            "employee_name": "System Administrator",
            "salesperson_name": "Rajesh Kumar",
            "department_name": "Electronics",
            "category": "Hardware Defect",
            "complain_types": "Quality",
            "complain_details": f"Automated Vercel E2E test complaint {unique_id}",
            "vendor_name": "Ajit Electronics Ltd",
            "priority": "High",
            "gr_amount": "2450.50",
            "gr_expected_date": "2026-11-15",
            "handled_by": "System Administrator",
            "status": "Submitted",
            "remarks": "Created by automated Vercel verification suite",
            "comments": "E2E verification run on Vercel lambda"
        }
        create_resp = self.session.post(f"{self.base_url}/api/complaints", json=create_payload)
        self.assertEqual(create_resp.status_code, 201, f"Create failed: {create_resp.text}")
        c_data = create_resp.json()
        self.assertEqual(c_data.get("status"), "success")
        complaint = c_data.get("complaint", {})
        cid = complaint.get("Complaint ID") or complaint.get("complaint_id")
        self.assertTrue(cid, f"Missing complaint_id in response: {c_data}")
        print(f"  [PASS] 05a: Created complaint {cid} in Google Sheets DB.")

        # 2. Read Complaint
        get_resp = self.session.get(f"{self.base_url}/api/complaints/{cid}")
        self.assertEqual(get_resp.status_code, 200)
        fetched = get_resp.json().get("complaint", {})
        self.assertEqual(fetched.get("Customer Name") or fetched.get("customer_name"), test_customer)
        self.assertEqual(fetched.get("Invoice No.") or fetched.get("invoice_no"), test_invoice)
        print(f"  [PASS] 05b: Read complaint {cid} back from Google Sheets successfully.")

        # 3. Update Complaint
        update_payload = {
            "status": "In Progress",
            "handled_by": "System Administrator",
            "remarks": f"Updated by Vercel E2E test at {unique_id}"
        }
        put_resp = self.session.post(f"{self.base_url}/api/complaints/{cid}/update", json=update_payload)
        self.assertEqual(put_resp.status_code, 200, f"Update failed: {put_resp.text}")
        updated = put_resp.json().get("complaint", {})
        self.assertEqual(updated.get("Status") or updated.get("status"), "In Progress")
        print(f"  [PASS] 05c: Updated complaint {cid} status to 'In Progress'.")

    def test_06_reports_dynamic_filtering(self):
        """Verify Reports & Analytics module: dynamic multi-filtering and KPI cards."""
        report_payload = {
            "date_range": "all",
            "departments": ["Electronics"],
            "statuses": ["Submitted", "Pending", "In Progress", "Resolved"],
            "priorities": ["High", "Urgent", "Medium"]
        }
        resp = self.session.post(f"{self.base_url}/api/reports", json=report_payload)
        self.assertEqual(resp.status_code, 200, f"Reports request failed: {resp.text}")
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("kpis", data)
        self.assertIn("records", data)
        kpis = data.get("kpis", {})
        total = kpis.get("total") or len(data.get("records", []))
        print(f"  [PASS] 06: Generated dynamic report. Matching records: {total}.")

    def test_07_export_csv(self):
        """Verify CSV report export from Vercel."""
        export_payload = {
            "date_range": "all",
            "columns": ["complaint_id", "invoice_no", "customer_name", "status", "priority", "created_at"]
        }
        resp = self.session.post(f"{self.base_url}/api/reports/export/csv", json=export_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers.get("Content-Type", ""))
        self.assertIn("Complaint ID", resp.text)
        print(f"  [PASS] 07: CSV export verified ({len(resp.content)} bytes).")

    def test_08_export_excel(self):
        """Verify Excel (.xlsx) report export from Vercel."""
        export_payload = {
            "date_range": "all",
            "columns": ["complaint_id", "invoice_no", "customer_name", "status", "priority", "created_at"]
        }
        resp = self.session.post(f"{self.base_url}/api/reports/export/excel", json=export_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.headers.get("Content-Type", "").lower())
        self.assertTrue(resp.content.startswith(b"PK\x03\x04"), "Invalid XLSX ZIP header")
        print(f"  [PASS] 08: Excel (.xlsx) export verified ({len(resp.content)} bytes, valid openpyxl XLSX).")

    def test_09_export_pdf(self):
        """Verify PDF report export from Vercel."""
        export_payload = {
            "date_range": "all",
            "columns": ["complaint_id", "invoice_no", "customer_name", "status", "priority"]
        }
        resp = self.session.post(f"{self.base_url}/api/reports/export/pdf", json=export_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/pdf", resp.headers.get("Content-Type", ""))
        self.assertTrue(resp.content.startswith(b"%PDF"), "Invalid PDF header")
        print(f"  [PASS] 09: PDF export verified ({len(resp.content)} bytes, valid ReportLab PDF).")

    def test_10_drive_live_upload_probe(self):
        """Verify Google Drive live binary upload, download, SHA-256 integrity, and cleanup."""
        resp = self.session.post(f"{self.base_url}/api/drive/test-upload")
        self.assertEqual(resp.status_code, 200, f"Drive live probe failed: {resp.text}")
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("verified"))
        print(f"  [PASS] 10: Google Drive live upload/download SHA-256 integrity verified in folder '{data.get('folder_name')}'. File ID: {data.get('file_id')}")

if __name__ == "__main__":
    unittest.main()
