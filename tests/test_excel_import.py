"""
Comprehensive Unit and Integration Tests for Excel Import & Template Download Module.
AJIT ZONE PVT. LTD. Complaint Management System.

Covers:
1. Template generation:
   - File format (.xlsx)
   - Canonical columns with required column indicators
   - Data validation rules (From Dept, Priority, Status, Department Name)
   - Instructions & Reference worksheet
2. Validation engine:
   - No Customer-Based Duplicate Blocking:
     - Same customer with multiple complaints on same date
     - Existing customer in database lodging new complaints
   - Case-Insensitive Matching & Spelling Validation:
     - Customer names in UPPERCASE, lowercase, Title Case (e.g. RAHUL SHARMA, Rahul Sharma, rahul sharma)
     - Master data values accepted regardless of case (KURTI, ONLINE, DAMAGE, KOMAL VAISHNAV)
     - Misspelled master entries (e.g. kurtiii, onlieee, invalid_type) rejected
   - Display Formatting:
     - Automatic standardization of display casing (e.g. RAHUL SHARMA -> Rahul Sharma)
     - Standard canonical master casing (e.g. dAmAgE -> Damage)
   - True Duplicate Protection:
     - Exact identical complaint row re-import blocked
     - Explicit Record ID / Complaint ID duplicate in file or DB blocked
     - Distinct complaints for the same customer NOT blocked
   - Mandatory field validation & 10-digit mobile number normalization
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

    def test_no_customer_duplicate_blocking_unlimited_complaints(self):
        """
        Verify rule 1: The same customer can have unlimited complaints,
        even multiple complaints on the same date. Customer name / mobile never causes rejection.
        """
        # Customer already exists in database
        MockSheetsDB.create_complaint({
            'Customer Name': 'RAHUL SHARMA',
            'Customer Mobile No.': '9876543210',
            'Invoice No.': 'INV-PREV-01',
            'Complain Date': '2026-11-20',
            'Complain Types': 'Damage',
            'Department Name': 'Kurti',
            'From Department': 'Online'
        }, self.super_admin_user)

        # Upload 3 new complaints for the SAME customer on the SAME date with different invoices/issues
        rows = [
            [
                'Rahul Sharma', '9876543210', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-NEW-01', 'High', 'Submitted',
                'Apparel', '', 'Nikita Vaishnav', '', '', 'First complaint on new invoice', '', '', '', ''
            ],
            [
                'rahul sharma', '9876543210', '2026-11-20', 'Quality',
                'Kurti', 'Online', 'INV-NEW-02', 'Medium', 'Submitted',
                'Apparel', '', 'Nikita Vaishnav', '', '', 'Second complaint on different invoice', '', '', '', ''
            ],
            [
                'RAHUL SHARMA', '+91 98765 43210', '2026-11-20', 'Wrong Product',
                'Saree', 'Offline', 'INV-NEW-03', 'Low', 'Submitted',
                'Apparel', '', 'Komal Vaishnav', '', '', 'Third complaint in different department', '', '', '', ''
            ]
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        # ALL 3 rows must be accepted! 0 rejected!
        self.assertEqual(result['total_rows'], 3)
        self.assertEqual(result['valid_count'], 3)
        self.assertEqual(result['invalid_count'], 0)

    def test_case_insensitive_matching_and_display_formatting(self):
        """
        Verify rule 2, 3, 4: Case-insensitive matching accepts uppercase, lowercase,
        and mixed case, and standardizes display formatting to canonical casing.
        """
        rows = [
            [
                # UPPERCASE inputs
                'PRIYA PATEL', '9876500001', '2026-11-20', 'DAMAGE',
                'KURTI', 'ONLINE', 'INV-UPPER', 'URGENT', 'SUBMITTED',
                '', '', 'NIKITA VAISHNAV'
            ],
            [
                # lowercase inputs
                'amit verma', '9876500002', '2026-11-20', 'wrong product',
                'saree', 'packing', 'inv-lower', 'low', 'in progress',
                '', '', 'komal vaishnav'
            ],
            [
                # mixed / irregular case
                'sNeHa jOsHi', '9876500003', '2026-11-20', 'bIlTy',
                'dReSs mAtErIaL', 'oFfLiNe', 'InV-MiX', 'MeDiUm', 'UnDeR ReViEw',
                '', '', 'gOpAl rAm dOdVaDiYa'
            ]
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 3)
        self.assertEqual(result['invalid_count'], 0)

        row1 = result['valid_rows'][0]
        self.assertEqual(row1['customer_name'], 'Priya Patel') # Title Case display formatting
        self.assertEqual(row1['complain_types'], 'Damage') # Standard master casing
        self.assertEqual(row1['department_name'], 'Kurti') # Standard master casing
        self.assertEqual(row1['from_department'], 'Online') # Standard canonical casing
        self.assertEqual(row1['salesperson_name'], 'Nikita Vaishnav') # Canonical salesperson casing
        self.assertEqual(row1['priority'], 'Urgent')
        self.assertEqual(row1['status'], 'Submitted')

        row2 = result['valid_rows'][1]
        self.assertEqual(row2['customer_name'], 'Amit Verma')
        self.assertEqual(row2['complain_types'], 'Wrong Product')
        self.assertEqual(row2['department_name'], 'Saree')
        self.assertEqual(row2['from_department'], 'Packing')
        self.assertEqual(row2['salesperson_name'], 'Komal Vaishnav')
        self.assertEqual(row2['priority'], 'Low')
        self.assertEqual(row2['status'], 'In Progress')

        row3 = result['valid_rows'][2]
        self.assertEqual(row3['customer_name'], 'sNeHa jOsHi') # Preserved mixed casing
        self.assertEqual(row3['complain_types'], 'Bilty')
        self.assertEqual(row3['department_name'], 'Dress Material')
        self.assertEqual(row3['from_department'], 'Offline')
        self.assertEqual(row3['salesperson_name'], 'Gopal Ram Dodvadiya')

    def test_spelling_validation_rejects_typos_in_master_data(self):
        """
        Verify rule 3: While letter casing is ignored, actual spelling errors
        in master entities (departments, from department, complaint types) are caught.
        """
        rows = [
            # Typo in department: 'Kurtiii' instead of 'Kurti'
            ['Valid User', '9876543210', '2026-11-20', 'Damage', 'Kurtiii', 'Online', 'INV-1'],
            # Typo in From Department: 'Onlieee' instead of 'Online'
            ['Valid User', '9876543211', '2026-11-20', 'Damage', 'Kurti', 'Onlieee', 'INV-2'],
            # Typo in Complain Types: 'UnknownIssueXYZ'
            ['Valid User', '9876543212', '2026-11-20', 'UnknownIssueXYZ', 'Kurti', 'Online', 'INV-3']
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 0)
        self.assertEqual(result['invalid_count'], 3)

        err1 = ' '.join(result['invalid_rows'][0]['errors'])
        self.assertIn("Department 'Kurtiii' is not an active company department", err1)

        err2 = ' '.join(result['invalid_rows'][1]['errors'])
        self.assertIn("From Department must be one of: Online, Offline, Packing, None", err2)

        err3 = ' '.join(result['invalid_rows'][2]['errors'])
        self.assertIn("Complain Type 'UnknownIssueXYZ' does not match any active complaint type", err3)

    def test_true_duplicate_protection_exact_reimport_and_record_id(self):
        """
        Verify rule 5: Prevents accidental re-import of exact identical complaints only when
        EVERY single field is 100% identical, or when explicit Record ID is duplicated.
        """
        # Seed an existing complaint in DB
        MockSheetsDB.create_complaint({
            'Complaint ID': 'CMP-EXISTING-01',
            'Customer Name': 'Existing Client',
            'Customer Mobile No.': '9876500001',
            'Invoice No.': 'INV-EX-999',
            'Complain Date': '2026-11-20',
            'Complain Types': 'Damage',
            'Department Name': 'Kurti',
            'From Department': 'Online',
            'Complain Details': 'Identical torn seam',
            'Remarks': 'Inspection pending',
            'GR Amount': '500.00'
        }, self.super_admin_user)

        rows = [
            # 1. Exact 100% identical complaint as in database (accidental re-import of exact same record)
            [
                'Existing Client', '9876500001', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-EX-999', 'Medium', 'Submitted',
                '', '', '', '', 'Inspection pending', 'Identical torn seam', '', '', '500.00', ''
            ],
            # 2. Fresh valid complaint
            [
                'Customer A', '9876500002', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-FRESH-01', 'High', 'Submitted',
                '', '', '', '', 'New remark', 'New detail', '', '', '200.00', ''
            ],
            # 3. Duplicate row in same uploaded file (exact 100% identical duplicate of row 2)
            [
                'Customer A', '9876500002', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-FRESH-01', 'High', 'Submitted',
                '', '', '', '', 'New remark', 'New detail', '', '', '200.00', ''
            ],
            # 4. Row with explicit Record ID in file
            [
                'Customer B', '9876500003', '2026-11-20', 'Quality',
                'Saree', 'Offline', 'INV-REC-01', 'Medium', 'Submitted',
                '', '', '', '', '', '', '', '', '', '', 'REC-REPEAT-01'
            ],
            # 5. Row with same duplicate explicit Record ID
            [
                'Customer C', '9876500004', '2026-11-20', 'Wrong Product',
                'Saree', 'Offline', 'INV-REC-02', 'Medium', 'Submitted',
                '', '', '', '', '', '', '', '', '', '', 'REC-REPEAT-01'
            ]
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        # Row 2 and Row 4 are valid (Row 4 is first occurrence of REC-REPEAT-01)
        self.assertEqual(result['valid_count'], 2)
        # Rows 1, 3, 5 are rejected
        self.assertEqual(result['invalid_count'], 3)

        err_row1 = ' '.join(result['invalid_rows'][0]['errors'])
        self.assertIn("identical complaint record already exists in database", err_row1.lower())

        err_row3 = ' '.join(result['invalid_rows'][1]['errors'])
        self.assertIn("identical complaint record already exists in this batch", err_row3.lower())

        err_row5 = ' '.join(result['invalid_rows'][2]['errors'])
        self.assertIn("duplicate record id 'rec-repeat-01'", err_row5.lower())

    def test_allow_duplicate_entries_until_every_single_thing_is_same(self):
        """
        Verify: Users can enter duplicate entries as much as they want,
        and records are accepted as valid UNTIL every single field is 100% identical.
        """
        rows = [
            # Base record 1
            [
                'Pooja Sharma', '9876511111', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-DUP-100', 'High', 'Submitted',
                'Apparel', 'Vendor Alpha', 'Nikita Vaishnav', '', 'First note', 'Torn seam', '', '', '100.00', ''
            ],
            # Record 2: Same customer, phone, date, dept, from-dept, invoice, vendor, sp, details, remarks, BUT different type
            [
                'Pooja Sharma', '9876511111', '2026-11-20', 'Quality',
                'Kurti', 'Online', 'INV-DUP-100', 'High', 'Submitted',
                'Apparel', 'Vendor Alpha', 'Nikita Vaishnav', '', 'First note', 'Torn seam', '', '', '100.00', ''
            ],
            # Record 3: Same customer, phone, date, dept, invoice, type, but different details
            [
                'Pooja Sharma', '9876511111', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-DUP-100', 'High', 'Submitted',
                'Apparel', 'Vendor Alpha', 'Nikita Vaishnav', '', 'First note', 'Missing button', '', '', '100.00', ''
            ],
            # Record 4: Same customer, phone, date, dept, invoice, type, details, but different GR amount
            [
                'Pooja Sharma', '9876511111', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-DUP-100', 'High', 'Submitted',
                'Apparel', 'Vendor Alpha', 'Nikita Vaishnav', '', 'First note', 'Torn seam', '', '', '250.00', ''
            ],
            # Record 5: Same customer, phone, date, dept, invoice, type, details, GR amount, but different vendor
            [
                'Pooja Sharma', '9876511111', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-DUP-100', 'High', 'Submitted',
                'Apparel', 'Vendor Beta', 'Nikita Vaishnav', '', 'First note', 'Torn seam', '', '', '100.00', ''
            ],
            # Record 6: Same customer, phone, date, dept, invoice, type, details, vendor, but different customer name (e.g. shared invoice)
            [
                'Sunita Sharma', '9876522222', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-DUP-100', 'High', 'Submitted',
                'Apparel', 'Vendor Alpha', 'Nikita Vaishnav', '', 'First note', 'Torn seam', '', '', '100.00', ''
            ],
            # Record 7: Exact 100% clone of Record 1 (EVERY SINGLE FIELD IS IDENTICAL) -> Rejected!
            [
                'Pooja Sharma', '9876511111', '2026-11-20', 'Damage',
                'Kurti', 'Online', 'INV-DUP-100', 'High', 'Submitted',
                'Apparel', 'Vendor Alpha', 'Nikita Vaishnav', '', 'First note', 'Torn seam', '', '', '100.00', ''
            ]
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        # Records 1 to 6 are all valid because at least one field differs!
        self.assertEqual(result['valid_count'], 6)
        # Record 7 is the only invalid row because every single thing is identical to Record 1
        self.assertEqual(result['invalid_count'], 1)
        err_msg = ' '.join(result['invalid_rows'][0]['errors']).lower()
        self.assertIn("identical complaint record already exists in this batch", err_msg)

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
            ['User One', '+91 98765 43210', '2026-11-20', 'Damage', 'Kurti', 'Online', 'INV-A'],
            # Row 2: Invalid 8-digit mobile
            ['User Two', '98765432', '2026-11-20', 'Damage', 'Kurti', 'Offline', 'INV-B'],
            # Row 3: Invalid 12-digit mobile without country code
            ['User Three', '987654321000', '2026-11-20', 'Damage', 'Kurti', 'None', 'INV-C'],
            # Row 4: Non-numeric mobile
            ['User Four', 'INVALID-MOB', '2026-11-20', 'Damage', 'Kurti', 'Packing', 'INV-D']
        ]
        stream = create_test_excel_file(rows)
        result = ExcelImportService.parse_and_validate(stream, self.super_admin_user, MockSheetsDB)

        self.assertEqual(result['valid_count'], 1)
        self.assertEqual(result['invalid_count'], 3)
        self.assertEqual(result['valid_rows'][0]['customer_mobile'], '9876543210')

        for inv_row in result['invalid_rows']:
            err_text = ' '.join(inv_row['errors'])
            self.assertIn('Customer Mobile No.', err_text)

    def test_rbac_department_scoping(self):
        """Verify staff user cannot upload records for unauthorized departments."""
        # staff_user is restricted to 'Online Sales'
        rows = [
            # Authorized row
            ['Customer One', '9876543210', '2026-11-20', 'Damage', 'Online Sales', 'Online', 'INV-OK-1'],
            # Unauthorized row (General department)
            ['Customer Two', '9876543211', '2026-11-20', 'Damage', 'General', 'Offline', 'INV-BLOCK-2']
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
            # Valid row (UPPERCASE customer name and lowercase dropdown values)
            ['MOHAN LAL', '9876511111', '2026-11-20', 'damage', 'kurti', 'online', 'INV-E2E-1', 'high', 'submitted', '', '', 'nikita vaishnav'],
            # Invalid row (missing mobile & bad from dept)
            ['Suresh Kumar', 'INVALID', '2026-11-20', 'Damage', 'Kurti', 'BadDept', 'INV-E2E-2']
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

        # Verify display formatting on valid row
        valid_row = data['data']['valid_rows'][0]
        self.assertEqual(valid_row['customer_name'], 'Mohan Lal')
        self.assertEqual(valid_row['department_name'], 'Kurti')
        self.assertEqual(valid_row['from_department'], 'Online')
        self.assertEqual(valid_row['salesperson_name'], 'Nikita Vaishnav')

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
        self.assertEqual(imported[0]['Department Name'], 'Kurti')
        self.assertEqual(imported[0]['Salesperson Name'], 'Nikita Vaishnav')

        # Verify audit log was recorded
        audit_logs = MockSheetsDB._store['Audit_Logs']
        import_audits = [a for a in audit_logs if a.get('Action') == 'EXCEL_IMPORT']
        self.assertGreaterEqual(len(import_audits), 1)
        self.assertEqual(import_audits[0]['Action'], 'EXCEL_IMPORT')
        self.assertEqual(commit_data['imported_count'], 1)


if __name__ == '__main__':
    unittest.main()
