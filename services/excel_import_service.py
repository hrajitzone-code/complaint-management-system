"""
Excel Import & Template Service for AJIT ZONE PVT. LTD. Complaint Management System.

Features:
- Downloadable .xlsx template with correct headers, sample format, and dropdown data validation.
- Instructions & Reference sheet containing active departments, complaint types, salespersons, and valid values.
- Bulk Excel parsing and comprehensive validation (mandatory fields, 10-digit mobile, dates, duplicate detection).
- RBAC & Department-level authorization checks per row.
- Preview generation of valid vs invalid records with specific error reasons.
- Downloadable error report (.xlsx) with highlighted rejected rows and error descriptions.
"""

import io
import re
import uuid
import time
import datetime
import logging
from typing import Dict, List, Any, Optional, Tuple

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from services.rbac_service import RBACService

logger = logging.getLogger(__name__)

# Temporary in-memory cache for preview validation batches
# Format: { preview_token: { 'valid_rows': [...], 'invalid_rows': [...], 'user_id': ..., 'timestamp': ... } }
_PREVIEW_CACHE: Dict[str, Dict[str, Any]] = {}


def _cleanup_preview_cache():
    """Prunes preview cache entries older than 30 minutes."""
    now_ts = time.time()
    expired = [k for k, v in _PREVIEW_CACHE.items() if now_ts - v.get('timestamp', 0) > 1800]
    for k in expired:
        _PREVIEW_CACHE.pop(k, None)


# Canonical template columns: (internal_key, Header Label, Required, Sample Value, Dropdown Formula)
TEMPLATE_COLUMNS: List[Tuple[str, str, bool, str, Optional[str]]] = [
    ('customer_name', 'Customer Name *', True, 'Anjali Gupta', None),
    ('customer_mobile', 'Customer Mobile No. *', True, '9876543210', None),
    ('complain_date', 'Complain Date * (YYYY-MM-DD)', True, '2026-11-20', None),
    ('complain_types', 'Complain Types *', True, 'Defective Stitching', None),
    ('department_name', 'Department Name *', True, 'Kurti', None),
    ('from_department', 'From Department *', True, 'Online', 'Online,Offline,Packing,None'),
    ('invoice_no', 'Invoice No.', False, 'INV-1001', None),
    ('priority', 'Priority', False, 'Medium', 'Low,Medium,High,Urgent'),
    ('status', 'Status', False, 'Submitted', 'Submitted,Under Review,In Progress,Resolved,Closed'),
    ('category', 'Category', False, 'Apparel', None),
    ('vendor_name', 'Vendor Name', False, 'Raj Textiles', None),
    ('salesperson_name', 'Salesperson Name', False, 'Rahul Kumar', None),
    ('handled_by', 'Handled By', False, 'NITA', None),
    ('remarks', 'Remarks', False, 'Customer reported loose stitch on sleeve hem', None),
    ('complain_details', 'Complain Details', False, 'Stitching defect near sleeve junction', None),
    ('closing_date', 'Closing Date (YYYY-MM-DD)', False, '', None),
    ('solution_given', 'Solution Given', False, '', None),
    ('gr_amount', 'GR Amount', False, '', None),
    ('gr_expected_date', 'GR Expected Date (YYYY-MM-DD)', False, '', None),
]

# Aliases for flexible header mapping
HEADER_ALIASES: Dict[str, str] = {
    'customername': 'customer_name',
    'customer': 'customer_name',
    'complainantname': 'customer_name',
    'clientname': 'customer_name',

    'customermobileno': 'customer_mobile',
    'customermobile': 'customer_mobile',
    'mobileno': 'customer_mobile',
    'mobile': 'customer_mobile',
    'phone': 'customer_mobile',
    'contactno': 'customer_mobile',

    'complaindate': 'complain_date',
    'complaintdate': 'complain_date',
    'date': 'complain_date',

    'complaintypes': 'complain_types',
    'complaintype': 'complain_types',
    'complainttypes': 'complain_types',
    'complainttype': 'complain_types',
    'issue': 'complain_types',
    'subject': 'complain_types',

    'departmentname': 'department_name',
    'department': 'department_name',
    'dept': 'department_name',

    'fromdepartment': 'from_department',
    'fromdept': 'from_department',
    'source': 'from_department',
    'origin': 'from_department',

    'invoiceno': 'invoice_no',
    'invoicenumber': 'invoice_no',
    'invoice': 'invoice_no',
    'billno': 'invoice_no',

    'priority': 'priority',
    'status': 'status',
    'category': 'category',

    'vendorname': 'vendor_name',
    'vendor': 'vendor_name',

    'salespersonname': 'salesperson_name',
    'salesperson': 'salesperson_name',
    'salesman': 'salesperson_name',

    'handledby': 'handled_by',
    'assignedto': 'handled_by',
    'handler': 'handled_by',

    'remarks': 'remarks',
    'remark': 'remarks',
    'notes': 'remarks',

    'complaindetails': 'complain_details',
    'complaintdetails': 'complain_details',
    'description': 'complain_details',
    'details': 'complain_details',

    'closingdate': 'closing_date',
    'resolutiondate': 'closing_date',

    'solutiongiven': 'solution_given',
    'solution': 'solution_given',
    'resolution': 'solution_given',

    'gramount': 'gr_amount',
    'amount': 'gr_amount',

    'grexpecteddate': 'gr_expected_date',
    'expecteddate': 'gr_expected_date'
}


class ExcelImportService:
    """Service handling Excel complaint template generation, parsing, validation, and error reporting."""

    @classmethod
    def generate_template(cls, db_engine=None, authorized_departments: Optional[List[str]] = None) -> io.BytesIO:
        """
        Generates a professional, branded Excel template with sample rows and dropdown validations.
        """
        wb = openpyxl.Workbook()

        # Styles
        navy_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        req_navy_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        req_header_font = Font(name="Calibri", size=11, bold=True, color="FDE047") # Yellow accent for required fields
        data_font = Font(name="Calibri", size=10)
        sample_font = Font(name="Calibri", size=10, italic=True, color="475569")
        thin_border_side = Side(style='thin', color='CBD5E1')
        grid_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
        center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
        left_align = Alignment(horizontal='left', vertical='center', wrap_text=True)

        # -------------------------------------------------------------
        # Sheet 1: Complaints Template
        # -------------------------------------------------------------
        ws = wb.active
        ws.title = "Complaints Template"
        ws.views.sheetView[0].showGridLines = True

        # Header Row
        headers = [col[1] for col in TEMPLATE_COLUMNS]
        ws.append(headers)
        ws.row_dimensions[1].height = 28

        for col_idx, (key, label, is_req, _, _) in enumerate(TEMPLATE_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = req_header_font if is_req else header_font
            cell.fill = req_navy_fill if is_req else navy_fill
            cell.alignment = center_align
            cell.border = grid_border

        # Sample Rows (Rows 2 & 3)
        sample_row_1 = [col[3] for col in TEMPLATE_COLUMNS]
        sample_row_2 = [
            'Sneha Rao', '9876543211', '2026-11-20', 'Color Bleeding', 'Saree',
            'Offline', 'INV-1002', 'High', 'Under Review', 'Silk', 'Vendor B',
            'Direct', 'NITA', 'Fabric bleeding color on first dry clean',
            'Customer requested replacement or credit note', '', '', '', ''
        ]
        ws.append(sample_row_1)
        ws.append(sample_row_2)

        for r_idx in (2, 3):
            ws.row_dimensions[r_idx].height = 22
            for col_idx in range(1, len(TEMPLATE_COLUMNS) + 1):
                cell = ws.cell(row=r_idx, column=col_idx)
                cell.font = sample_font
                cell.alignment = left_align
                cell.border = grid_border

        # -------------------------------------------------------------
        # Sheet 2: Instructions & Reference Data
        # -------------------------------------------------------------
        ws_ref = wb.create_sheet(title="Instructions & Reference")
        ws_ref.views.sheetView[0].showGridLines = True

        # Title
        ws_ref.cell(row=1, column=1, value="Ajit Zone Pvt. Ltd. — Complaints Excel Import Guide").font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
        ws_ref.cell(row=2, column=1, value="Please adhere to the format rules below. Asterisk (*) denotes a mandatory column.").font = Font(name="Calibri", size=10, italic=True)

        instructions = [
            ("Field Name", "Requirement", "Allowed Format / Accepted Values"),
            ("Customer Name *", "Mandatory", "Text (min 2 characters, e.g. Anjali Gupta)"),
            ("Customer Mobile No. *", "Mandatory", "Exactly 10 numeric digits (e.g. 9876543210)"),
            ("Complain Date *", "Mandatory", "Date formatted as YYYY-MM-DD (e.g. 2026-11-20)"),
            ("Complain Types *", "Mandatory", "Issue category or description (e.g. Defective Stitching, Color Bleeding)"),
            ("Department Name *", "Mandatory", "Company department (must match authorized department list below)"),
            ("From Department *", "Mandatory", "Online, Offline, Packing, None"),
            ("Invoice No.", "Optional", "Customer bill or invoice number (e.g. INV-1001)"),
            ("Priority", "Optional", "Low, Medium, High, Urgent (Defaults to Medium if blank)"),
            ("Status", "Optional", "Submitted, Under Review, In Progress, Resolved, Closed (Defaults to Submitted)"),
            ("Closing Date", "Optional", "YYYY-MM-DD (Must be on or after Complain Date)"),
            ("GR Amount", "Optional", "Numeric amount (e.g. 1500.00)"),
        ]

        ws_ref.cell(row=4, column=1, value="Format Specifications:").font = Font(name="Calibri", size=11, bold=True)
        for i, (fn, req, rule) in enumerate(instructions, start=5):
            c1 = ws_ref.cell(row=i, column=1, value=fn)
            c2 = ws_ref.cell(row=i, column=2, value=req)
            c3 = ws_ref.cell(row=i, column=3, value=rule)
            c1.border = grid_border
            c2.border = grid_border
            c3.border = grid_border
            if i == 5:
                c1.fill = navy_fill
                c2.fill = navy_fill
                c3.fill = navy_fill
                c1.font = header_font
                c2.font = header_font
                c3.font = header_font

        # Active Departments list in reference sheet
        active_depts = []
        if db_engine:
            try:
                dept_records = db_engine.get_active_departments()
                active_depts = [d.get('Name') for d in dept_records if d.get('Name')]
            except Exception:
                pass
        if not active_depts:
            active_depts = [
                'Online', 'Dress Material', 'Readymade', 'Top', 'Kurti',
                'Lehenga', 'Gown', 'Saree', 'Kids', 'Undergarments',
                'Nighty', 'Dupatta', 'Bottom', 'Complaint'
            ]
        if authorized_departments:
            active_depts = [d for d in active_depts if d in authorized_departments or '*' in authorized_departments or 'ALL' in authorized_departments]

        ref_start_row = len(instructions) + 7
        ws_ref.cell(row=ref_start_row, column=1, value="Active Company Departments:").font = Font(name="Calibri", size=11, bold=True)
        dept_start_row = ref_start_row + 1
        for idx, dept_name in enumerate(active_depts, start=dept_start_row):
            d_cell = ws_ref.cell(row=idx, column=1, value=dept_name)
            d_cell.font = data_font
            d_cell.border = grid_border
        dept_end_row = dept_start_row + len(active_depts) - 1

        # -------------------------------------------------------------
        # Dropdown Data Validations on Template Sheet
        # -------------------------------------------------------------
        try:
            # 1. From Department Dropdown (Column F: F2:F1000)
            dv_from = DataValidation(type="list", formula1='"Online,Offline,Packing,None"', allow_blank=False)
            dv_from.errorTitle = "Invalid From Department"
            dv_from.error = "Please select Online, Offline, Packing, or None."
            ws.add_data_validation(dv_from)
            dv_from.add("F2:F1000")

            # 2. Priority Dropdown (Column H: H2:H1000)
            dv_prio = DataValidation(type="list", formula1='"Low,Medium,High,Urgent"', allow_blank=True)
            dv_prio.errorTitle = "Invalid Priority"
            dv_prio.error = "Please select Low, Medium, High, or Urgent."
            ws.add_data_validation(dv_prio)
            dv_prio.add("H2:H1000")

            # 3. Status Dropdown (Column I: I2:I1000)
            dv_stat = DataValidation(type="list", formula1='"Submitted,Under Review,In Progress,Resolved,Closed"', allow_blank=True)
            dv_stat.errorTitle = "Invalid Status"
            dv_stat.error = "Please select a valid complaint status."
            ws.add_data_validation(dv_stat)
            dv_stat.add("I2:I1000")

            # 4. Department Name Dropdown (Column E: E2:E1000)
            if active_depts:
                dept_formula = f"'Instructions & Reference'!$A${dept_start_row}:$A${dept_end_row}"
                dv_dept = DataValidation(type="list", formula1=dept_formula, allow_blank=False)
                dv_dept.errorTitle = "Invalid Department"
                dv_dept.error = "Please select a valid company department from the list."
                ws.add_data_validation(dv_dept)
                dv_dept.add("E2:E1000")
        except Exception as dv_err:
            logger.warning(f"Could not apply data validation dropdowns: {dv_err}")

        # Auto-adjust column widths on both sheets
        for sheet in (ws, ws_ref):
            for col in sheet.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    val_str = str(cell.value or '')
                    if '\n' in val_str:
                        val_str = max(val_str.split('\n'), key=len)
                    max_len = max(max_len, len(val_str))
                sheet.column_dimensions[col_letter].width = max(max_len + 4, 14)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    @classmethod
    def _normalize_header(cls, text: str) -> str:
        """Strips asterisks, parentheticals, spaces, punctuation, and lowercases."""
        if not text:
            return ""
        clean = re.sub(r'\(.*?\)', '', str(text))
        clean = re.sub(r'[^a-zA-Z0-9]', '', clean).lower()
        return clean.strip()

    @classmethod
    def _parse_date(cls, val: Any) -> Optional[str]:
        """Parses dates from string, datetime, or date objects to YYYY-MM-DD."""
        if not val:
            return None
        if isinstance(val, (datetime.date, datetime.datetime)):
            return val.strftime('%Y-%m-%d')
        val_str = str(val).strip()
        if not val_str:
            return None
        # Try common formats
        for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d', '%m/%d/%Y', '%Y-%m-%d %H:%M:%S'):
            try:
                dt = datetime.datetime.strptime(val_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                pass
        return None

    @classmethod
    def _clean_mobile(cls, val: Any) -> Optional[str]:
        """Cleans and validates 10-digit Indian mobile number."""
        if val is None:
            return None
        raw = str(val).split('.')[0].strip() # Handle float from excel e.g. 9876543210.0
        cleaned = re.sub(r'[^\d]', '', raw)
        # Strip country code +91 or leading 0 if 11 or 12 digits
        if len(cleaned) == 12 and cleaned.startswith('91'):
            cleaned = cleaned[2:]
        elif len(cleaned) == 11 and cleaned.startswith('0'):
            cleaned = cleaned[1:]
        if len(cleaned) == 10:
            return cleaned
        return None

    @classmethod
    def parse_and_validate(cls, file_stream, current_user: Dict[str, Any], db_engine=None) -> Dict[str, Any]:
        """
        Parses uploaded Excel workbook, validates each row against mandatory fields,
        master data, RBAC department scope, and duplicate detection.
        Returns preview data and preview token.
        """
        _cleanup_preview_cache()

        if not current_user:
            raise ValueError("User authentication required for Excel import.")

        try:
            wb = openpyxl.load_workbook(file_stream, data_only=True)
        except Exception as e:
            raise ValueError(f"Unable to parse Excel file. Please ensure it is a valid .xlsx file. Error: {e}")

        # Find target worksheet: prefer 'Complaints Template', else first sheet
        ws = None
        for name in wb.sheetnames:
            if 'template' in name.lower() or 'complaint' in name.lower():
                ws = wb[name]
                break
        if not ws:
            ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows or len(rows) < 2:
            raise ValueError("The uploaded Excel sheet contains no data rows.")

        # Map header row
        header_row = rows[0]
        col_to_key: Dict[int, str] = {}
        sorted_aliases = sorted(HEADER_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
        for idx, h in enumerate(header_row):
            norm = cls._normalize_header(h)
            if not norm:
                continue
            if norm in HEADER_ALIASES:
                col_to_key[idx] = HEADER_ALIASES[norm]
            else:
                for k, mapped in sorted_aliases:
                    if k == norm or k in norm or norm in k:
                        col_to_key[idx] = mapped
                        break

        # Check required columns exist in headers
        required_keys = {'customer_name', 'customer_mobile', 'complain_date', 'complain_types', 'department_name'}
        mapped_keys = set(col_to_key.values())
        missing_required = required_keys - mapped_keys
        if missing_required:
            raise ValueError(f"Missing mandatory columns in Excel header: {', '.join(sorted(missing_required))}. Please use the official template.")

        # Fetch active reference data from db_engine
        active_dept_map: Dict[str, str] = {} # lower -> canonical
        existing_complaint_keys = set()
        user_depts = [d.lower() for d in RBACService.get_user_departments(current_user)]
        user_has_all_scope = '*' in user_depts or 'all' in user_depts or current_user.get('role') in ('Super Admin', 'Admin')

        if db_engine:
            try:
                if hasattr(db_engine, 'get_active_departments'):
                    raw_depts = db_engine.get_active_departments()
                elif hasattr(db_engine, 'get_all_departments'):
                    raw_depts = db_engine.get_all_departments(include_inactive=False)
                else:
                    raw_depts = []

                for d in raw_depts:
                    name = (d.get('Name') or d.get('Department Name') or '').strip()
                    if name:
                        active_dept_map[name.lower()] = name
            except Exception as e:
                logger.warning(f"Could not load active departments: {e}")

            try:
                # Load existing complaints for duplicate checking
                existing = db_engine.get_all_complaints()
                for ec in existing:
                    inv = (ec.get('Invoice No.') or '').strip().lower()
                    mob = (ec.get('Customer Mobile No.') or '').strip()
                    c_date = (ec.get('Complain Date') or '').strip()
                    c_type = (ec.get('Complain Types') or '').strip().lower()
                    if inv and mob:
                        existing_complaint_keys.add(f"inv:{inv}|mob:{mob}")
                    if mob and c_date and c_type:
                        existing_complaint_keys.add(f"mob:{mob}|date:{c_date}|type:{c_type}")
            except Exception as e:
                logger.warning(f"Could not load existing complaints for duplicate check: {e}")

        # Merge standard company departments into active_dept_map
        company_defaults = [
            'General', 'Online Sales', 'Online', 'Dress Material', 'Readymade',
            'Top', 'Kurti', 'Lehenga', 'Gown', 'Saree', 'Kids',
            'Undergarments', 'Nighty', 'Dupatta', 'Bottom', 'Complaint',
            'IT & Technical Support', 'Customer Operations', 'Finance & Billing'
        ]
        for d in company_defaults:
            if d.lower() not in active_dept_map:
                active_dept_map[d.lower()] = d

        valid_rows: List[Dict[str, Any]] = []
        invalid_rows: List[Dict[str, Any]] = []
        in_file_duplicate_keys = set()

        sample_name_patterns = {'anjali gupta', 'sneha rao', 'sample customer', 'customer name'}

        for r_num, row in enumerate(rows[1:], start=2):
            # Check if entire row is empty
            if not row or all(v is None or str(v).strip() == '' for v in row):
                continue

            # Build row dict
            row_dict: Dict[str, Any] = {}
            for col_idx, key in col_to_key.items():
                val = row[col_idx] if col_idx < len(row) else None
                row_dict[key] = val

            # Check if this is the sample data row from our template
            cust_name_check = str(row_dict.get('customer_name') or '').strip().lower()
            if cust_name_check in sample_name_patterns and r_num in (2, 3):
                # Sample row, skip
                continue

            errors: List[str] = []

            # 1. Customer Name
            c_name = str(row_dict.get('customer_name') or '').strip()
            if not c_name or len(c_name) < 2:
                errors.append("Customer Name is required (minimum 2 characters).")

            # 2. Customer Mobile No.
            raw_mob = row_dict.get('customer_mobile')
            c_mob = cls._clean_mobile(raw_mob)
            if not c_mob:
                if not raw_mob or str(raw_mob).strip() == '':
                    errors.append("Customer Mobile No. is required (must be exactly 10 digits).")
                else:
                    errors.append(f"Customer Mobile No. '{raw_mob}' is invalid (must be exactly 10 digits).")

            # 3. Complain Date
            raw_date = row_dict.get('complain_date')
            c_date = cls._parse_date(raw_date)
            if not c_date:
                if not raw_date or str(raw_date).strip() == '':
                    errors.append("Complain Date is required (use YYYY-MM-DD).")
                else:
                    errors.append(f"Complain Date '{raw_date}' is invalid (use YYYY-MM-DD).")

            # 4. Complain Types
            c_type = str(row_dict.get('complain_types') or '').strip()
            if not c_type:
                errors.append("Complain Types is required.")

            # 5. Department Name & RBAC authorization
            raw_dept = str(row_dict.get('department_name') or '').strip()
            if not raw_dept:
                errors.append("Department Name is required.")
            else:
                canon_dept = active_dept_map.get(raw_dept.lower())
                if not canon_dept:
                    errors.append(f"Department '{raw_dept}' is not an active company department.")
                else:
                    row_dict['department_name'] = canon_dept
                    # RBAC authorization check: Does this user have access to this department?
                    if not user_has_all_scope and canon_dept.lower() not in user_depts:
                        errors.append(f"Unauthorized department '{canon_dept}'. You are only permitted to lodge complaints for: {', '.join(user_depts)}.")

            # 6. From Department
            raw_from_dept = str(row_dict.get('from_department') or 'None').strip()
            if not raw_from_dept or raw_from_dept.lower() in ('none', 'n/a', '-', ''):
                canon_from_dept = 'None'
            else:
                canon_from_dept = {
                    'online': 'Online',
                    'offline': 'Offline',
                    'packing': 'Packing',
                    'none': 'None'
                }.get(raw_from_dept.lower())
                if not canon_from_dept:
                    errors.append(f"From Department must be one of: Online, Offline, Packing, None (received '{raw_from_dept}').")
            row_dict['from_department'] = canon_from_dept or 'None'

            # 7. Priority
            raw_prio = str(row_dict.get('priority') or 'Medium').strip().capitalize()
            if raw_prio not in ('Low', 'Medium', 'High', 'Urgent'):
                raw_prio = 'Medium'
            row_dict['priority'] = raw_prio

            # 8. Status
            raw_status = str(row_dict.get('status') or 'Submitted').strip()
            valid_statuses = {'submitted': 'Submitted', 'under review': 'Under Review', 'in progress': 'In Progress', 'resolved': 'Resolved', 'closed': 'Closed'}
            canon_status = valid_statuses.get(raw_status.lower(), 'Submitted')
            row_dict['status'] = canon_status

            # 9. Invoice No.
            c_inv = str(row_dict.get('invoice_no') or '').strip()
            row_dict['invoice_no'] = c_inv

            # 10. Dates: Closing Date & GR Expected Date
            c_closing = cls._parse_date(row_dict.get('closing_date')) if row_dict.get('closing_date') else ''
            if row_dict.get('closing_date') and not c_closing:
                errors.append(f"Closing Date '{row_dict.get('closing_date')}' is invalid (use YYYY-MM-DD).")
            elif c_closing and c_date and c_closing < c_date:
                errors.append(f"Closing Date ({c_closing}) cannot be earlier than Complain Date ({c_date}).")
            row_dict['closing_date'] = c_closing or ''

            c_gr_date = cls._parse_date(row_dict.get('gr_expected_date')) if row_dict.get('gr_expected_date') else ''
            if row_dict.get('gr_expected_date') and not c_gr_date:
                errors.append(f"GR Expected Date '{row_dict.get('gr_expected_date')}' is invalid (use YYYY-MM-DD).")
            row_dict['gr_expected_date'] = c_gr_date or ''

            # 11. GR Amount
            raw_gr = str(row_dict.get('gr_amount') or '').strip()
            if raw_gr:
                try:
                    gr_val = float(raw_gr.replace(',', ''))
                    if gr_val < 0:
                        errors.append("GR Amount cannot be negative.")
                    row_dict['gr_amount'] = f"{gr_val:.2f}"
                except ValueError:
                    errors.append(f"GR Amount '{raw_gr}' must be numeric.")
            else:
                row_dict['gr_amount'] = ''

            # 12. Duplicate Check
            if c_mob and c_inv:
                inv_mob_key = f"inv:{c_inv.lower()}|mob:{c_mob}"
                if inv_mob_key in in_file_duplicate_keys:
                    errors.append(f"Duplicate row in uploaded file (Invoice '{c_inv}' and Mobile '{c_mob}' already present).")
                elif inv_mob_key in existing_complaint_keys:
                    errors.append(f"Complaint with Invoice '{c_inv}' and Mobile '{c_mob}' already exists in database.")
                else:
                    in_file_duplicate_keys.add(inv_mob_key)

            if c_mob and c_date and c_type:
                mob_date_key = f"mob:{c_mob}|date:{c_date}|type:{c_type.lower()}"
                if mob_date_key in in_file_duplicate_keys:
                    errors.append(f"Duplicate complaint in uploaded file (same mobile, date, and issue type).")
                elif mob_date_key in existing_complaint_keys:
                    errors.append(f"Matching complaint already exists in database.")
                else:
                    in_file_duplicate_keys.add(mob_date_key)

            # Clean remaining strings
            for k in ('category', 'vendor_name', 'salesperson_name', 'handled_by', 'remarks', 'complain_details', 'solution_given'):
                row_dict[k] = str(row_dict.get(k) or '').strip()

            row_summary = {
                'row_number': r_num,
                'customer_name': c_name,
                'customer_mobile': c_mob or str(raw_mob or ''),
                'complain_date': c_date or str(raw_date or ''),
                'complain_types': c_type,
                'department_name': row_dict.get('department_name') or raw_dept,
                'from_department': row_dict.get('from_department') or raw_from_dept,
                'invoice_no': c_inv,
                'priority': row_dict.get('priority', 'Medium'),
                'status': row_dict.get('status', 'Submitted'),
                'category': row_dict.get('category', ''),
                'vendor_name': row_dict.get('vendor_name', ''),
                'salesperson_name': row_dict.get('salesperson_name', ''),
                'handled_by': row_dict.get('handled_by', ''),
                'remarks': row_dict.get('remarks', ''),
                'complain_details': row_dict.get('complain_details', ''),
                'closing_date': row_dict.get('closing_date', ''),
                'solution_given': row_dict.get('solution_given', ''),
                'gr_amount': row_dict.get('gr_amount', ''),
                'gr_expected_date': row_dict.get('gr_expected_date', ''),
                'errors': errors
            }

            if errors:
                invalid_rows.append(row_summary)
            else:
                valid_rows.append(row_summary)

        preview_token = uuid.uuid4().hex
        _PREVIEW_CACHE[preview_token] = {
            'valid_rows': valid_rows,
            'invalid_rows': invalid_rows,
            'user_id': current_user.get('user_id'),
            'user_email': current_user.get('email'),
            'timestamp': time.time()
        }

        return {
            'total_rows': len(valid_rows) + len(invalid_rows),
            'valid_count': len(valid_rows),
            'invalid_count': len(invalid_rows),
            'valid_rows': valid_rows,
            'invalid_rows': invalid_rows,
            'preview_token': preview_token
        }

    @classmethod
    def get_cached_preview(cls, preview_token: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached validation results by preview token."""
        _cleanup_preview_cache()
        return _PREVIEW_CACHE.get(preview_token)

    @classmethod
    def generate_error_report(cls, invalid_rows: List[Dict[str, Any]]) -> io.BytesIO:
        """
        Generates an Excel workbook containing all invalid rows with their specific error reasons.
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Rejected Rows - Errors"
        ws.views.sheetView[0].showGridLines = True

        # Styles
        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        error_hdr_fill = PatternFill(start_color="991B1B", end_color="991B1B", fill_type="solid")
        error_cell_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")

        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        error_font = Font(name="Calibri", size=10, bold=True, color="991B1B")
        data_font = Font(name="Calibri", size=10)

        thin_side = Side(style='thin', color='CBD5E1')
        grid_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        # Header Columns
        headers = ["Row #", "Error Reasons"] + [col[1] for col in TEMPLATE_COLUMNS]
        ws.append(headers)
        ws.row_dimensions[1].height = 28

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = error_hdr_fill if col_idx == 2 else header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = grid_border

        for r_idx, row in enumerate(invalid_rows, start=2):
            ws.row_dimensions[r_idx].height = 24
            err_text = chr(8226) + ' ' + (' \n' + chr(8226) + ' ').join(row.get('errors', []))
            row_data = [
                row.get('row_number', r_idx),
                err_text,
                row.get('customer_name', ''),
                row.get('customer_mobile', ''),
                row.get('complain_date', ''),
                row.get('complain_types', ''),
                row.get('department_name', ''),
                row.get('from_department', ''),
                row.get('invoice_no', ''),
                row.get('priority', ''),
                row.get('status', ''),
                row.get('category', ''),
                row.get('vendor_name', ''),
                row.get('salesperson_name', ''),
                row.get('handled_by', ''),
                row.get('remarks', ''),
                row.get('complain_details', ''),
                row.get('closing_date', ''),
                row.get('solution_given', ''),
                row.get('gr_amount', ''),
                row.get('gr_expected_date', '')
            ]

            ws.append(row_data)

            # Apply cell styles
            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=r_idx, column=col_idx)
                cell.border = grid_border
                cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
                if col_idx == 2:
                    cell.fill = error_cell_fill
                    cell.font = error_font
                else:
                    cell.font = data_font

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if '\n' in val_str:
                    val_str = max(val_str.split('\n'), key=len)
                max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 14), 50)
        ws.column_dimensions['B'].width = 45 # Error Reasons column

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf
