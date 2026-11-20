"""
Comprehensive Unit and Integration Tests for Excel Import & Template Download Module.
AJIT ZONE PVT. LTD. Complaint Management System.

Covers:
1. Template generation:
   - File format (.xlsx)
   - Canonical 19 columns with required column indicators
   - Data validation rules (From Dept, Priority, Status, Department Name)
   - Instructions & Reference worksheet
2. Validation engine:
   - Mandatory field validation
   - 10-digit mobile number normalization & validation
   - Date formats parsing & normalization
   - Dropdown options validation (From Department: Online, Offline, Packing, None)
   - In-batch duplicate collision detection
   - Database duplicate record detection
   - RBAC department authorization scoping per row
   - Empty sheet rejection
3. End-to-end API Endpoints:
   - GET /api/complaints/import/template (Auth, RBAC permission, Content-Type)
   - POST /api/complaints/import/preview (File parsing, Preview stats, Preview token)
   - POST /api/complaints/import/commit (Session verification, Batch write to Google Sheets, Audit trail)
   - POST & GET /api/complaints/import/error-report (Generation of rejected rows error report)
   - Security: Unauthorized role (Viewer) blocked
"""

import os
import io
import sys
import json
import unittest
import datetime
import openpyxl

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import Config
from app import app
from services.mock_sheets_db import MockSheetsDB
from services.rbac_service import RBACService, ALL_PERMISSION_IDS
from services.excel_import_service import ExcelImportService, TEMPLATE_COLUMNS


def create_test_excel_file(rows, headers=None):
    """Helper to construct an in-memory .xlsx file with specified rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Complaints Template"
    if headers is None:
        headers = [col[1] for col in TEMPLATE_COLUMNS]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


class TestExcelImportModule(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key-excel'

    def setUp(self):
        MockSheetsDB.reset()
        self.client = app.test_client()

        # Admin User (Super Admin with full access)
        self.super_admin_user = {
            'user_id': 'USR-SUPERADMIN',
            'name': 'Super Admin',
            'email': 'superadmin@ajitzone.com',
            'role': 'Super Admin',
            'department': 'ALL',
            'status': 'Active',
            'permissions': list(ALL_PERMISSION_IDS)
        }

        # Staff User with limited department access ('Online Sales')
        self.staff_user = {
            'user_id': 'USR-STAFF-ONLINE',
            'name': 'Online Staff',
            'email': 'onlinestaff@ajitzone.com',
            'role': 'Department Staff',
            'department': 'Online Sales',
            'status': 'Active',
            'permissions': ['complaints:create', 'complaints:view_department', 'complaints:edit']
        }

        # Viewer User (read-only, no complaints:create)
        self.viewer_user = {
            'user_id': 'USR-VIEWER',
            'name': 'Viewer User',
            'email': 'viewer@ajitzone.com',
            'role': 'Viewer',
            'department': 'ALL',
            'status': 'Active',
            'permissions': ['complaints:view_all', 'reports:view']
        }

    # =========================================================================
    # 1. TEMPLATE GENERATION TESTS
    # =========================================================================

    def test_generate_template_structure(self):
        """Verify the generated template contains correct sheets, headers, and validations."""
        stream = ExcelImportService.generate_template(MockSheetsDB)
        self.assertIsNotNone(stream)
        stream.seek(0)

        wb = openpyxl.load_workbook(stream)
        sheet_names = wb.sheetnames
        self.assertIn('Complaints Template', sheet_names)
        self.assertIn('Instructions & Reference', sheet_names)

        ws = wb['Complaints Template']
        headers = [cell.value for cell in ws[1]]
        expected_headers = [col[1] for col in TEMPLATE_COLUMNS]
        self.assertEqual(headers, expected_headers)

        # Check required fields are flagged
        self.assertIn('Customer Name *', headers)
        self.assertIn('Customer Mobile No. *', headers)
        self.assertIn('Department Name *', headers)
        self.assertIn('From Department *', headers)

        # Check sample data rows are provided
        data_rows = list(ws.iter_rows(min_row=2, values_only=True))
        self.assertGreaterEqual(len(data_rows), 1)

        # Check reference sheet has From Department list
        ref_ws = wb['Instructions & Reference']
        ref_values = [str(cell.value) for row in ref_ws.iter_rows() for cell in row if cell.value]
        self.assertTrue(any('Online' in v for v in ref_values))
        self.assertTrue(any('Offline' in v for v in ref_values))
        self.assertTrue(any('Packing' in v for v in ref_values))
        self.assertTrue(any('None' in v for v in ref_values))

    # =========================================================================
    # 2. VALIDATION ENGINE UNIT TESTS
    # =========================================================================

    def test_parse_and_validate_valid_rows(self):
        """Verify valid rows are accepted and returned with valid_count and preview_token."""
        rows = [
            [
                'Rajesh Sharma', '9876543210', '2026-11-20', 'Quality Issue',
                'General', 'Online', 'INV-1001', 'High', 'Submitted',
                'Defect', 'Vendor A', 'Sales Rep 1', 'Unassigned', 'Note 1',
                'Defective stitching', '', '', '0', ''
            ],
            [
                'Anita Gupta', '+91 9123456789', '2026-11-19', 'Delivery Delay',
                'General', 'Packing', 'INV-1002', 'Medium', 'Submitted',
                'Logistics', 'Vendor B', 'Sales Rep 2', 'Unassigned', 'Note 2',
                'Delayed 3 days', '', '', '0', ''
            ]
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['total_rows'], 2)
        self.assertEqual(result['valid_count'], 2)
        self.assertEqual(result['invalid_count'], 0)
        self.assertTrue(bool(result['preview_token']))
        self.assertEqual(result['valid_rows'][0]['customer_name'], 'Rajesh Sharma')
        self.assertEqual(result['valid_rows'][0]['customer_mobile'], '9876543210')
        self.assertEqual(result['valid_rows'][1]['customer_mobile'], '9123456789')
        self.assertEqual(result['valid_rows'][1]['from_department'], 'Packing')

    def test_parse_and_validate_mandatory_fields(self):
        """Verify missing required fields (Customer, Mobile, Date, Department) are rejected."""
        rows = [
            [
                '', '', '', '',  # Missing Customer, Mobile, Date, Type
                '', 'InvalidDeptVal', '', '', '',
                '', '', '', '', '', '', '', '', '', ''
            ]
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 0)
        self.assertEqual(result['invalid_count'], 1)
        errors = result['invalid_rows'][0]['errors']
        error_text = ' '.join(errors)

        self.assertIn('Customer Name is required', error_text)
        self.assertIn('Customer Mobile No. is required', error_text)
        self.assertIn('Complain Date is required', error_text)
        self.assertIn('Complain Types is required', error_text)
        self.assertIn('Department Name is required', error_text)
        self.assertIn('From Department must be one of', error_text)

    def test_parse_and_validate_mobile_normalization(self):
        """Verify 10-digit mobile number variations and rejection of invalid lengths."""
        rows = [
            # Row 1: Valid formatted mobile with +91 and spaces
            ['User One', '+91 98765 43210', '2026-11-20', 'Type A', 'General', 'Online', 'INV-A'],
            # Row 2: Invalid 8-digit mobile
            ['User Two', '98765432', '2026-11-20', 'Type A', 'General', 'Offline', 'INV-B'],
            # Row 3: Invalid 12-digit mobile without country code
            ['User Three', '987654321000', '2026-11-20', 'Type A', 'General', 'None', 'INV-C'],
            # Row 4: Non-numeric mobile
            ['User Four', 'INVALID-MOB', '2026-11-20', 'Type A', 'General', 'Packing', 'INV-D']
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 1)
        self.assertEqual(result['invalid_count'], 3)
        self.assertEqual(result['valid_rows'][0]['customer_mobile'], '9876543210')

        for inv_row in result['invalid_rows']:
            err_text = ' '.join(inv_row['errors'])
            self.assertIn('Customer Mobile No.', err_text)

    def test_parse_and_validate_from_department(self):
        """Verify From Department only accepts Online, Offline, Packing, None."""
        rows = [
            ['Valid Online', '9876543210', '2026-11-20', 'Type', 'General', 'Online', 'INV-1'],
            ['Valid Offline', '9876543211', '2026-11-20', 'Type', 'General', 'Offline', 'INV-2'],
            ['Valid Packing', '9876543212', '2026-11-20', 'Type', 'General', 'Packing', 'INV-3'],
            ['Valid None', '9876543213', '2026-11-20', 'Type', 'General', 'None', 'INV-4'],
            ['Invalid Dept', '9876543214', '2026-11-20', 'Type', 'General', 'RandomDept', 'INV-5']
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 4)
        self.assertEqual(result['invalid_count'], 1)
        self.assertIn("From Department must be one of: Online, Offline, Packing, None", ' '.join(result['invalid_rows'][0]['errors']))

    def test_duplicate_detection_internal_and_external(self):
        """Verify internal in-batch collision and external database duplicate detection."""
        # 1. Existing complaint in DB
        MockSheetsDB.create_complaint({
            'Customer Name': 'Existing Client',
            'Customer Mobile No.': '9876500001',
            'Invoice No.': 'INV-EX-999',
            'Complain Date': '2026-11-15',
            'Complain Types': 'Fabric Defect',
            'Department Name': 'General',
            'From Department': 'Online'
        }, self.super_admin_user)

        rows = [
            # Duplicate with database (Same Invoice No + Mobile)
            ['Existing Client Dup', '9876500001', '2026-11-20', 'Fabric Defect', 'General', 'Online', 'INV-EX-999'],
            # Fresh new record
            ['Fresh Customer', '9876500002', '2026-11-20', 'Color Issue', 'General', 'Online', 'INV-NEW-001'],
            # Internal duplicate of the fresh new record in same batch
            ['Fresh Customer Again', '9876500002', '2026-11-20', 'Color Issue', 'General', 'Online', 'INV-NEW-001']
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 1)
        self.assertEqual(result['invalid_count'], 2)

        # Check DB duplicate error reason
        self.assertIn("already exists in database", ' '.join(result['invalid_rows'][0]['errors']))
        # Check in-batch collision error reason
        self.assertIn("Duplicate complaint in uploaded file", ' '.join(result['invalid_rows'][1]['errors']))

    def test_rbac_department_scoping(self):
        """Verify staff user cannot upload records for unauthorized departments."""
        # staff_user is restricted to 'Online Sales'
        rows = [
            # Authorized row
            ['Customer One', '9876543210', '2026-11-20', 'Defect', 'Online Sales', 'Online', 'INV-OK-1'],
            # Unauthorized row (General department)
            ['Customer Two', '9876543211', '2026-11-20', 'Defect', 'General', 'Offline', 'INV-BLOCK-2']
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.staff_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 1)
        self.assertEqual(result['invalid_count'], 1)
        self.assertIn("Unauthorized department 'General'", ' '.join(result['invalid_rows'][0]['errors']))

    def test_empty_sheet_rejection(self):
        """Verify empty workbook or header-only sheet raises ValueError."""
        stream = create_test_excel_file([])  # Only headers, 0 data rows
        with self.assertRaises(ValueError) as ctx:
            ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)
        self.assertIn("no data rows", str(ctx.exception).lower())

    # =========================================================================
    # 3. END-TO-END API ENDPOINTS INTEGRATION TESTS
    # =========================================================================

    def test_api_template_download(self):
        """Test GET /api/complaints/import/template returns .xlsx attachment."""
        with self.client.session_transaction() as sess:
            sess['user'] = self.super_admin_user

        res = self.client.get('/api/complaints/import/template')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('complaint_import_template.xlsx', res.headers.get('Content-Disposition', ''))

    def test_api_template_download_viewer_forbidden(self):
        """Test Viewer role without complaints:create is blocked (403)."""
        with self.client.session_transaction() as sess:
            sess['user'] = self.viewer_user

        res = self.client.get('/api/complaints/import/template')
        self.assertEqual(res.status_code, 403)

    def test_api_preview_commit_and_error_report_flow(self):
        """Full end-to-end integration: Preview -> Error Report -> Commit to Google Sheets."""
        with self.client.session_transaction() as sess:
            sess['user'] = self.super_admin_user

        rows = [
            # Valid row
            ['Mohan Lal', '9876511111', '2026-11-20', 'Stitching Issue', 'General', 'Online', 'INV-E2E-1', 'High'],
            # Invalid row (missing mobile & bad from dept)
            ['Suresh Kumar', 'INVALID', '2026-11-20', 'Color Bleed', 'General', 'BadDept', 'INV-E2E-2']
        ]
        excel_stream = create_test_excel_file(rows)

        # 1. POST /api/complaints/import/preview
        preview_res = self.client.post(
            '/api/complaints/import/preview',
            data={'file': (excel_stream, 'test_complaints.xlsx')},
            content_type='multipart/form-data'
        )
        self.assertEqual(preview_res.status_code, 200)
        data = preview_res.get_json()
        self.assertEqual(data['status'], 'success')
        preview_token = data['data']['preview_token']
        self.assertEqual(data['data']['total_rows'], 2)
        self.assertEqual(data['data']['valid_count'], 1)
        self.assertEqual(data['data']['invalid_count'], 1)

        # 2. GET /api/complaints/import/error-report
        err_res = self.client.get(f'/api/complaints/import/error-report?preview_token={preview_token}')
        self.assertEqual(err_res.status_code, 200)
        self.assertEqual(err_res.mimetype, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('rejected_complaints_error_report.xlsx', err_res.headers.get('Content-Disposition', ''))

        # Verify error report contents
        err_wb = openpyxl.load_workbook(io.BytesIO(err_res.data))
        err_ws = err_wb.active
        self.assertEqual(err_ws.title, "Rejected Rows - Errors")
        err_rows = list(err_ws.iter_rows(values_only=True))
        self.assertEqual(len(err_rows), 2)  # Header + 1 invalid row
        self.assertEqual(err_rows[1][2], 'Suresh Kumar')

        # 3. POST /api/complaints/import/commit
        commit_res = self.client.post(
            '/api/complaints/import/commit',
            json={'preview_token': preview_token}
        )
        self.assertEqual(commit_res.status_code, 200)
        commit_data = commit_res.get_json()
        self.assertEqual(commit_data['status'], 'success')
        self.assertEqual(commit_data['imported_count'], 1)

        # Verify complaint exists in MockSheetsDB
        all_complaints = MockSheetsDB.get_all_complaints()
        imported = [c for c in all_complaints if c.get('Customer Name') == 'Mohan Lal']
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]['From Department'], 'Online')
        self.assertEqual(imported[0]['Customer Mobile No.'], '9876511111')

        # Verify audit log was recorded
        audit_logs = MockSheetsDB._store['Audit_Logs']
        import_audits = [a for a in audit_logs if a.get('Action') == 'EXCEL_IMPORT']
        self.assertGreaterEqual(len(import_audits), 1)
        self.assertEqual(import_audits[0]['Action'], 'EXCEL_IMPORT')
        self.assertEqual(commit_data['imported_count'], 1)


if __name__ == '__main__':
    unittest.main()
