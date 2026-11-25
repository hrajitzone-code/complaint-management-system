"""
Excel Import & Smart Validation Service for AJIT ZONE PVT. LTD. Complaint Management System.

Features:
- Downloadable .xlsx template with correct headers, sample format, and dropdown data validation.
- Instructions & Reference sheet containing active departments, complaint types, salespersons, and valid values.
- Smart Validation:
  - Case-insensitive matching and normalization to standard software display format.
  - Multi-department recognition (e.g. 'Kurti, Readymade') and common aliases (e.g. 'Dress' -> 'Dress Material', 'Kidswear' -> 'Kids', 'Duppta' -> 'Dupatta').
  - Status mapping (e.g. 'CLEARED' -> 'Resolved', 'HOLD' -> 'Under Review').
  - Salesperson normalization ('Other', first-name matching 'Nikita' -> 'Nikita Vaishnav', accepts custom salespersons in Title Case without blocking).
  - Indian 10-digit and international mobile number (+44, +61, +60, +64) recognition.
  - Flexible date format parsing.
  - No Customer-Based Duplicate Blocking: Customers can have unlimited complaints.
  - True Duplicate Protection: Prevents accidental re-import of exact identical complaints via Record ID or non-customer record fingerprint.
- Editable Preview & In-Memory Revalidation:
  - Revalidate modified rows in real time.
  - Return master options for frontend editable dropdowns.
- Downloadable error report (.xlsx) for remaining rejected rows.
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
    ('complain_types', 'Complain Types *', True, 'Damage', None),
    ('department_name', 'Department Name *', True, 'Kurti', None),
    ('from_department', 'From Department *', True, 'Online', 'Online,Offline,Packing,None'),
    ('invoice_no', 'Invoice No.', False, 'INV-1001', None),
    ('priority', 'Priority', False, 'Medium', 'Low,Medium,High,Urgent'),
    ('status', 'Status', False, 'Submitted', 'Submitted,Under Review,In Progress,Resolved,Closed'),
    ('category', 'Category', False, 'Apparel', None),
    ('vendor_name', 'Vendor Name', False, 'Raj Textiles', None),
    ('salesperson_name', 'Salesperson Name', False, 'Nikita Vaishnav', None),
    ('handled_by', 'Handled By', False, 'NITA', None),
    ('remarks', 'Remarks', False, 'Customer reported loose stitch on sleeve hem', None),
    ('complain_details', 'Complain Details', False, 'Stitching defect near sleeve junction', None),
    ('closing_date', 'Closing Date (YYYY-MM-DD)', False, '', None),
    ('solution_given', 'Solution Given', False, '', None),
    ('gr_amount', 'GR Amount', False, '', None),
    ('gr_expected_date', 'GR Expected Date (YYYY-MM-DD)', False, '', None),
    ('record_id', 'Record ID (Optional)', False, '', None)
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
    'salesrep': 'salesperson_name',

    'handledby': 'handled_by',
    'assignedto': 'handled_by',
    'handler': 'handled_by',

    'remarks': 'remarks',
    'remark': 'remarks',
    'notes': 'remarks',

    'complaindetails': 'complain_details',
    'complaintdetails': 'complain_details',
    'details': 'complain_details',
    'description': 'complain_details',

    'closingdate': 'closing_date',
    'closeddate': 'closing_date',
    'resolutiondate': 'closing_date',

    'solutiongiven': 'solution_given',
    'solution': 'solution_given',
    'resolution': 'solution_given',

    'gramount': 'gr_amount',
    'goodsreturnamount': 'gr_amount',
    'returnamount': 'gr_amount',

    'grexpecteddate': 'gr_expected_date',
    'expectedgrdate': 'gr_expected_date',

    'recordid': 'record_id',
    'importid': 'record_id',
    'complaintid': 'record_id',
    'complainid': 'record_id',
    'uniqueid': 'record_id',
    'externalid': 'record_id',
    'referenceid': 'record_id'
}

# Standard Master Data Constants
COMPANY_DEPARTMENT_ALIASES: Dict[str, str] = {
    'dress material': 'Dress Material', 'dress': 'Dress Material', 'dresses': 'Dress Material',
    'kids': 'Kids', 'kidswear': 'Kids', 'kids wear': 'Kids', 'kid wear': 'Kids',
    'top': 'Top', 'tops': 'Top',
    'kurti': 'Kurti', 'kurtis': 'Kurti',
    'saree': 'Saree', 'sarees': 'Saree',
    'bottom': 'Bottom', 'bottoms': 'Bottom', 'bottom wear': 'Bottom', 'bottomwear': 'Bottom',
    'nighty': 'Nighty', 'nighties': 'Nighty',
    'peticot': 'Bottom', 'peticoat': 'Bottom', 'petticoat': 'Bottom',
    'blouse & accessories': 'Blouse & Accessories', 'blouse': 'Blouse & Accessories', 'blouses': 'Blouse & Accessories',
    'dupatta': 'Dupatta', 'duppta': 'Dupatta', 'duppata': 'Dupatta', 'duppatta': 'Dupatta',
    'readymade': 'Readymade', 'redymade': 'Readymade', 'ready made': 'Readymade',
    'undergarments': 'Undergarments', 'undergarmnts': 'Undergarments', 'undergarment': 'Undergarments',
    'lehenga': 'Lehenga', 'lehengas': 'Lehenga',
    'gown': 'Gown', 'gowns': 'Gown',
    'online': 'Online', 'online sales': 'Online Sales', 'packing': 'Packing',
    'complaint': 'Complaint', 'complaints': 'Complaint',
    'general': 'General', 'other': 'General', 'others': 'General', 'none': 'General'
}

SALESPERSON_FIRST_NAME_MAP: Dict[str, str] = {
    'nikita': 'Nikita Vaishnav',
    'komal': 'Komal Vaishnav',
    'gopal': 'Gopal Ram Dodvadiya',
    'gopal ram': 'Gopal Ram Dodvadiya',
    'manisha': 'Manisha Sirvi',
    'nita': 'Nita Chaudhary',
    'rahul': 'Rahul Rajpurohit',
    'jayraj': 'Jayraj Solanki',
    'vikram': 'Vikram Rao',
    'priya': 'Priya Sharma'
}

STATUS_MAP: Dict[str, str] = {
    'cleared': 'Resolved', 'clear': 'Resolved', 'resolved': 'Resolved',
    'closed': 'Closed', 'close': 'Closed', 'hold': 'Under Review',
    'on hold': 'Under Review', 'pending': 'Under Review',
    'in progress': 'In Progress', 'inprogress': 'In Progress', 'working': 'In Progress',
    'submitted': 'Submitted', 'new': 'Submitted', 'open': 'Submitted'
}

STANDARD_COMPLAINT_TYPES: Dict[str, str] = {
    'damage': 'Damage', 'other': 'Other', 'quality': 'Quality',
    'packing': 'Packing', 'wrong product': 'Wrong Product',
    'item mismatch': 'Item Mismatch', 'rate issue': 'Rate issue',
    'product defect': 'Product Defect', 'bilty': 'Bilty',
    'transit damage': 'Transit Damage', 'billing discrepancy': 'Billing Discrepancy',
    'quality issue': 'Quality Issue', 'defective stitching': 'Defective Stitching',
    'delivery delay': 'Delivery Delay', 'fabric defect': 'Fabric Defect', 'color issue': 'Color Issue'
}

VALID_FROM_DEPTS: Dict[str, str] = {
    'online': 'Online', 'offline': 'Offline', 'packing': 'Packing', 'none': 'None'
}

VALID_PRIORITIES: Dict[str, str] = {
    'low': 'Low', 'medium': 'Medium', 'high': 'High', 'urgent': 'Urgent'
}


class ExcelImportService:
    """Service to handle Excel template generation, validation, preview editing, and error reporting."""

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

        # Sheet 1: Complaints Template
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
            ws.column_dimensions[get_column_letter(col_idx)].width = max(len(label) + 4, 16)

        # Sample Rows (Rows 2 & 3)
        sample_row_1 = [col[3] for col in TEMPLATE_COLUMNS]
        sample_row_2 = [
            'Rahul Sharma', '9876500001', '2026-11-20', 'Damage',
            'Dress Material', 'Offline', 'INV-1002', 'High', 'Submitted',
            'Fabric', 'Kalyan Mills', 'Komal Vaishnav', 'NITA',
            'Packing seal broken during cargo delivery', 'Cargo transit damage', '', '', '2450.00', '2026-11-25', ''
        ]

        ws.append(sample_row_1)
        ws.append(sample_row_2)

        for row_idx in (2, 3):
            ws.row_dimensions[row_idx].height = 22
            for col_idx in range(1, len(TEMPLATE_COLUMNS) + 1):
                c = ws.cell(row=row_idx, column=col_idx)
                c.font = sample_font
                c.border = grid_border
                c.alignment = left_align

        # Data Validations (Dropdowns on Sheet 1)
        dv_from_dept = DataValidation(type="list", formula1='"Online,Offline,Packing,None"', allow_blank=False)
        dv_from_dept.error = "Please choose a valid From Department (Online, Offline, Packing, None)."
        dv_from_dept.errorTitle = "Invalid From Department"
        ws.add_data_validation(dv_from_dept)
        dv_from_dept.add(f"F2:F2000")

        dv_priority = DataValidation(type="list", formula1='"Low,Medium,High,Urgent"', allow_blank=True)
        dv_priority.error = "Please select Low, Medium, High, or Urgent."
        dv_priority.errorTitle = "Invalid Priority"
        ws.add_data_validation(dv_priority)
        dv_priority.add(f"H2:H2000")

        dv_status = DataValidation(type="list", formula1='"Submitted,Under Review,In Progress,Resolved,Closed"', allow_blank=True)
        dv_status.error = "Please select a standard Complaint Status."
        dv_status.errorTitle = "Invalid Status"
        ws.add_data_validation(dv_status)
        dv_status.add(f"I2:I2000")

        # Sheet 2: Instructions & Reference
        ws_ref = wb.create_sheet(title="Instructions & Reference")
        ws_ref.views.sheetView[0].showGridLines = True

        ws_ref.cell(row=1, column=1, value="Ajit Zone Pvt. Ltd. - Complaints Excel Import Guide").font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
        ws_ref.cell(row=2, column=1, value="Please adhere to the format rules below. Asterisk (*) denotes a mandatory column.").font = Font(name="Calibri", size=10, italic=True)

        instructions = [
            ("Field Name", "Requirement", "Allowed Format / Accepted Values"),
            ("Customer Name *", "Mandatory", "Text (e.g. Rahul Sharma, accepted in ANY case: RAHUL SHARMA, rahul sharma)"),
            ("Customer Mobile No. *", "Mandatory", "10 numeric digits for India or international with country code (e.g. +44). Unlimited complaints per customer allowed!"),
            ("Complain Date *", "Mandatory", "Date formatted as YYYY-MM-DD or standard date format"),
            ("Complain Types *", "Mandatory", "Master complaint type (Damage, Other, Quality, Packing, Wrong Product, Item Mismatch, Rate issue, Bilty)"),
            ("Department Name *", "Mandatory", "Active company department (Kurti, Saree, Dress Material, Readymade, Top, Gown, Kids, etc. Multi-dept supported)"),
            ("From Department *", "Mandatory", "Online, Offline, Packing, None (case-insensitive)"),
            ("Invoice No.", "Optional", "Customer bill or invoice number (e.g. INV-1001)"),
            ("Priority", "Optional", "Low, Medium, High, Urgent (Defaults to Medium if blank)"),
            ("Status", "Optional", "Submitted, Under Review, In Progress, Resolved, Closed (Defaults to Submitted)"),
            ("Salesperson Name", "Optional", "Salesperson name from master data or 'Other' (case-insensitive)"),
            ("Closing Date", "Optional", "YYYY-MM-DD (Must be on or after Complain Date)"),
            ("GR Amount", "Optional", "Numeric amount (e.g. 1500.00)"),
            ("Record ID", "Optional", "Unique external identifier to prevent duplicate re-import (e.g. REC-1001)")
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

        active_depts = []
        if db_engine:
            try:
                if hasattr(db_engine, 'get_active_departments'):
                    dept_records = db_engine.get_active_departments()
                elif hasattr(db_engine, 'get_all_departments'):
                    dept_records = db_engine.get_all_departments(include_inactive=False)
                else:
                    dept_records = []
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
            cell = ws_ref.cell(row=idx, column=1, value=dept_name)
            cell.font = data_font
            cell.border = grid_border

        from_dept_start_row = ref_start_row
        ws_ref.cell(row=from_dept_start_row, column=3, value="Valid 'From Department' Values:").font = Font(name="Calibri", size=11, bold=True)
        for idx, fd in enumerate(['Online', 'Offline', 'Packing', 'None'], start=from_dept_start_row + 1):
            cell = ws_ref.cell(row=idx, column=3, value=fd)
            cell.font = data_font
            cell.border = grid_border

        ws_ref.column_dimensions['A'].width = 28
        ws_ref.column_dimensions['B'].width = 16
        ws_ref.column_dimensions['C'].width = 50

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
        for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d', '%m/%d/%Y', '%Y-%m-%d %H:%M:%S', '%d.%m.%Y', '%m.%d.%Y'):
            try:
                dt = datetime.datetime.strptime(val_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                pass
        return None

    @classmethod
    def _clean_mobile(cls, val: Any) -> Optional[str]:
        """
        Cleans and validates mobile numbers. Accepts Indian 10-digit numbers
        as well as international numbers with country codes (e.g. +44, +61, +60, +64).
        """
        if val is None:
            return None
        raw_str = str(val).split('.')[0].strip()
        if not raw_str:
            return None

        # International with explicit '+'
        if raw_str.startswith('+'):
            digits = re.sub(r'[^\d]', '', raw_str)
            if digits.startswith('91') and len(digits) == 12:
                return digits[2:]
            if 8 <= len(digits) <= 16:
                return '+' + digits
            return None

        # Clean non-digits
        digits = re.sub(r'[^\d]', '', raw_str)

        # Indian numbers with country code 91 or leading 0
        if len(digits) == 12 and digits.startswith('91'):
            return digits[2:]
        if len(digits) == 11 and digits.startswith('0'):
            return digits[1:]
        if len(digits) == 10:
            return digits

        # International numbers without leading + (e.g. 44..., 61..., 60..., 64...)
        if len(digits) > 10 and any(digits.startswith(code) for code in ('44', '61', '60', '64', '971', '1')):
            return '+' + digits

        return None

    @classmethod
    def _clean_department(cls, raw_dept: Any, active_dept_map: Dict[str, str]) -> Tuple[str, Optional[str]]:
        """
        Resolves single or multi-department strings, applying case-insensitivity,
        spelling aliases, and combining them canonically.
        """
        if not raw_dept:
            return '', "Department Name is required."

        raw_str = str(raw_dept).strip()
        parts = [p.strip() for p in re.split(r'[,;/|]+', raw_str) if p.strip()]
        if not parts:
            return '', "Department Name is required."

        canon_parts = []
        errors = []
        for p in parts:
            p_clean = re.sub(r'\s+', ' ', p).strip()
            p_low = p_clean.lower()
            if p_low in COMPANY_DEPARTMENT_ALIASES:
                canon = COMPANY_DEPARTMENT_ALIASES[p_low]
            elif p_low in active_dept_map:
                canon = active_dept_map[p_low]
            else:
                canon = p_clean.title()
                errors.append(f"Department '{p}' is not an active company department.")

            if canon and canon not in canon_parts:
                canon_parts.append(canon)

        res_str = ', '.join(canon_parts)
        err_str = ' '.join(errors) if errors else None
        return res_str, err_str

    @classmethod
    def _clean_salesperson(cls, raw_sp: Any, active_sps_map: Dict[str, str]) -> str:
        """
        Normalizes salesperson names: case-insensitive, maps first names to full names,
        accepts 'Other'/'OTHER', and standardizes custom names in Title Case without blocking.
        """
        if not raw_sp:
            return ''
        sp_clean = re.sub(r'\s+', ' ', str(raw_sp)).strip()
        sp_lower = sp_clean.lower()
        if sp_lower in ('none', 'n/a', '-', '', 'null'):
            return ''
        if sp_lower in ('other', 'others'):
            return 'Other'

        # First-name / alias mapping
        if sp_lower in SALESPERSON_FIRST_NAME_MAP:
            return SALESPERSON_FIRST_NAME_MAP[sp_lower]
        if sp_lower in active_sps_map:
            return active_sps_map[sp_lower]

        # Check prefix match against active salespersons
        for k, full_name in active_sps_map.items():
            if sp_lower == k.split(' ')[0]:
                return full_name

        # Standard Title Case for other salespersons (never reject complaint)
        return sp_clean.title()

    @classmethod
    def _clean_complain_type(cls, raw_type: Any, active_types_map: Dict[str, str]) -> Tuple[str, Optional[str]]:
        """
        Normalizes complain type: case-insensitive matching against master complaint types.
        """
        if not raw_type:
            return '', "Complain Types is required."
        t_clean = re.sub(r'\s+', ' ', str(raw_type)).strip()
        t_lower = t_clean.lower()

        if t_lower in active_types_map:
            return active_types_map[t_lower], None
        if t_lower in STANDARD_COMPLAINT_TYPES:
            return STANDARD_COMPLAINT_TYPES[t_lower], None

        # Check keyword in text e.g. "damage", "quality", "wrong product", "packing"
        for k, canon in STANDARD_COMPLAINT_TYPES.items():
            if k in t_lower:
                return canon, None

        # Return error if not an active master complaint type
        return t_clean.title(), f"Complain Type '{t_clean}' does not match any active complaint type."

    @classmethod
    def _load_master_data(cls, db_engine) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str], set, set, set]:
        """Loads master reference data and existing complaint records."""
        active_dept_map: Dict[str, str] = {}
        active_types_map: Dict[str, str] = {}
        active_salespersons_map: Dict[str, str] = {}

        existing_complaint_ids = set()
        existing_record_ids = set()
        existing_record_fingerprints = set()

        if db_engine:
            # 1. Departments
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

            # 2. Complaint Types
            try:
                if hasattr(db_engine, 'get_active_complaint_types'):
                    raw_types = db_engine.get_active_complaint_types()
                elif hasattr(db_engine, 'get_all_complaint_types'):
                    raw_types = [
                        t for t in db_engine.get_all_complaint_types()
                        if str(t.get('Is Active', 'TRUE')).upper() in ('TRUE', '1', 'YES')
                    ]
                else:
                    raw_types = []

                for t in raw_types:
                    name = (t.get('Name') or t.get('Complain Types') or '').strip()
                    if name:
                        active_types_map[name.lower()] = name
            except Exception as e:
                logger.warning(f"Could not load active complaint types: {e}")

            # 3. Salespersons
            try:
                if hasattr(db_engine, 'get_active_salespersons'):
                    raw_sps = db_engine.get_active_salespersons()
                elif hasattr(db_engine, 'get_all_salespersons'):
                    raw_sps = [
                        s for s in db_engine.get_all_salespersons()
                        if str(s.get('Is Active', 'TRUE')).upper() in ('TRUE', '1', 'YES')
                    ]
                else:
                    raw_sps = []

                for s in raw_sps:
                    name = (s.get('Name') or s.get('Salesperson Name') or '').strip()
                    if name:
                        active_salespersons_map[name.lower()] = name
            except Exception as e:
                logger.warning(f"Could not load active salespersons: {e}")

            # 4. Existing complaints for True Duplicate checking (Blocked ONLY when every single field is identical)
            try:
                existing = db_engine.get_all_complaints()
                for ec in existing:
                    cid = str(ec.get('Complaint ID') or '').strip().upper()
                    if cid:
                        existing_complaint_ids.add(cid)
                    rec_id = str(ec.get('Record ID') or ec.get('Import ID') or '').strip().upper()
                    if rec_id and rec_id not in ('-', 'N/A', 'NA', 'NONE', 'NULL'):
                        existing_record_ids.add(rec_id)

                    # 100% full-record identical fingerprint: ALL fields must be identical
                    ec_cname = str(ec.get('Customer Name') or '').strip().lower()
                    ec_mob_raw = str(ec.get('Customer Mobile No.') or ec.get('Customer Mobile') or '').strip()
                    ec_mob = re.sub(r'\D', '', ec_mob_raw)
                    if len(ec_mob) > 10:
                        ec_mob = ec_mob[-10:]
                    ec_date = str(ec.get('Complain Date') or '').strip()
                    ec_type = str(ec.get('Complain Types') or '').strip().lower()
                    ec_dept = str(ec.get('Department Name') or ec.get('Department') or '').strip().lower()
                    ec_from = str(ec.get('From Department') or 'None').strip().lower()
                    if not ec_from:
                        ec_from = 'none'
                    ec_inv = str(ec.get('Invoice No.') or '').strip().lower()
                    ec_cat = str(ec.get('Category') or '').strip().lower()
                    ec_vendor = str(ec.get('Vendor Name') or ec.get('Vendor') or '').strip().lower()
                    ec_sp = str(ec.get('Salesperson Name') or ec.get('Salesperson') or '').strip().lower()
                    ec_details = str(ec.get('Complain Details') or '').strip().lower()
                    ec_remarks = str(ec.get('Remarks') or '').strip().lower()
                    ec_prio = str(ec.get('Priority') or 'Medium').strip().lower()
                    ec_status = str(ec.get('Status') or 'Submitted').strip().lower()
                    ec_handler = str(ec.get('Handled By') or '').strip().lower()
                    if ec_handler in ('unassigned', 'none', ''):
                        ec_handler = ''
                    ec_gr_raw = str(ec.get('GR Amount') or '0.00').strip()
                    try:
                        ec_gr = f"{float(ec_gr_raw.replace(',', '')):.2f}"
                    except Exception:
                        ec_gr = "0.00"
                    ec_closing = str(ec.get('Closing Date') or '').strip()
                    ec_sol = str(ec.get('Solution Given') or '').strip().lower()
                    ec_gr_exp = str(ec.get('GR Expected Date') or '').strip()

                    fp = (
                        f"{ec_cname}|{ec_mob}|{ec_date}|{ec_type}|{ec_dept}|{ec_from}|{ec_inv}|"
                        f"{ec_cat}|{ec_vendor}|{ec_sp}|{ec_details}|{ec_remarks}|{ec_prio}|"
                        f"{ec_status}|{ec_handler}|{ec_gr}|{ec_closing}|{ec_sol}|{ec_gr_exp}"
                    )
                    existing_record_fingerprints.add(fp)
            except Exception as e:
                logger.warning(f"Could not load existing complaints for duplicate check: {e}")

        # Merge standard defaults
        for k, v in COMPANY_DEPARTMENT_ALIASES.items():
            if k not in active_dept_map:
                active_dept_map[k] = v

        for k, v in STANDARD_COMPLAINT_TYPES.items():
            if k not in active_types_map:
                active_types_map[k] = v

        for k, v in SALESPERSON_FIRST_NAME_MAP.items():
            if k not in active_salespersons_map:
                active_salespersons_map[k] = v
        active_salespersons_map['other'] = 'Other'

        return active_dept_map, active_types_map, active_salespersons_map, existing_complaint_ids, existing_record_ids, existing_record_fingerprints

    @classmethod
    def get_master_options(cls, db_engine=None) -> Dict[str, Any]:
        """Returns lists of valid master data options for frontend editable dropdowns."""
        active_dept_map, active_types_map, active_salespersons_map, _, _, _ = cls._load_master_data(db_engine)

        depts = sorted(list(dict.fromkeys(active_dept_map.values())))
        types = sorted(list(dict.fromkeys(active_types_map.values())))
        salespersons = sorted(list(dict.fromkeys(active_salespersons_map.values())))

        return {
            'departments': depts,
            'complain_types': types,
            'salespersons': salespersons,
            'from_departments': ['Online', 'Offline', 'Packing', 'None'],
            'priorities': ['Low', 'Medium', 'High', 'Urgent'],
            'statuses': ['Submitted', 'Under Review', 'In Progress', 'Resolved', 'Closed']
        }

    @classmethod
    def validate_single_row(
        cls,
        row_dict: Dict[str, Any],
        r_num: int,
        current_user: Dict[str, Any],
        active_dept_map: Dict[str, str],
        active_types_map: Dict[str, str],
        active_salespersons_map: Dict[str, str],
        existing_complaint_ids: set,
        existing_record_ids: set,
        existing_record_fingerprints: set,
        in_file_record_ids: set,
        in_file_fingerprints: set
    ) -> Dict[str, Any]:
        """Validates and normalizes a single complaint row using smart rules."""
        errors: List[str] = []
        user_depts = [d.lower() for d in RBACService.get_user_departments(current_user)]
        user_has_all_scope = '*' in user_depts or 'all' in user_depts or current_user.get('role') in ('Super Admin', 'Admin')

        # 1. Customer Name (Title Case display format; case-insensitive acceptance)
        raw_c_name = str(row_dict.get('customer_name') or '').strip()
        if not raw_c_name or len(raw_c_name) < 2:
            errors.append("Customer Name is required (minimum 2 characters).")
        c_name_display = raw_c_name.title() if (raw_c_name.isupper() or raw_c_name.islower()) else raw_c_name

        # 2. Customer Mobile No. (Indian 10-digit or international +country_code; NO customer duplicate blocking!)
        raw_mob = row_dict.get('customer_mobile')
        c_mob = cls._clean_mobile(raw_mob)
        if not c_mob:
            if not raw_mob or str(raw_mob).strip() == '':
                errors.append("Customer Mobile No. is required (must be 10 digits or include country code like +44).")
            else:
                errors.append(f"Customer Mobile No. '{raw_mob}' is invalid (must be 10 digits or include country code like +44).")
        mob_val = c_mob or str(raw_mob or '')

        # 3. Complain Date
        raw_date = row_dict.get('complain_date')
        c_date = cls._parse_date(raw_date)
        if not c_date:
            if not raw_date or str(raw_date).strip() == '':
                errors.append("Complain Date is required (use YYYY-MM-DD).")
            else:
                errors.append(f"Complain Date '{raw_date}' is invalid (use YYYY-MM-DD).")
        date_val = c_date or str(raw_date or '')

        # 4. Complain Types
        c_type, type_err = cls._clean_complain_type(row_dict.get('complain_types'), active_types_map)
        if type_err:
            errors.append(type_err)

        # 5. Department Name & RBAC Check
        canon_dept, dept_err = cls._clean_department(row_dict.get('department_name'), active_dept_map)
        if dept_err:
            errors.append(dept_err)
        elif canon_dept:
            if not user_has_all_scope:
                # Check department access
                row_parts = [p.strip() for p in canon_dept.split(',')]
                unauth = [p for p in row_parts if p.lower() not in user_depts]
                if unauth:
                    errors.append(f"Unauthorized department '{', '.join(unauth)}'. You are only permitted to lodge complaints for: {', '.join(user_depts)}.")

        # 6. From Department
        raw_from = str(row_dict.get('from_department') or 'None').strip()
        from_low = raw_from.lower()
        if not raw_from or from_low in ('none', 'n/a', '-', '', 'null'):
            canon_from = 'None'
        else:
            canon_from = VALID_FROM_DEPTS.get(from_low)
            if not canon_from:
                errors.append(f"From Department must be one of: Online, Offline, Packing, None (received '{raw_from}').")
                canon_from = 'None'

        # 7. Priority
        raw_prio = str(row_dict.get('priority') or 'Medium').strip().lower()
        canon_prio = VALID_PRIORITIES.get(raw_prio, 'Medium')

        # 8. Status (e.g. CLEARED -> Resolved, HOLD -> Under Review)
        raw_status = str(row_dict.get('status') or 'Submitted').strip()
        canon_status = STATUS_MAP.get(raw_status.lower(), 'Submitted')

        # 9. Salesperson Name (Other, first-name mapping, or standard Title Case)
        canon_sp = cls._clean_salesperson(row_dict.get('salesperson_name'), active_salespersons_map)

        # 10. Invoice No.
        c_inv = str(row_dict.get('invoice_no') or '').strip().upper()

        # 11. Details & Remarks
        c_details = str(row_dict.get('complain_details') or '').strip()
        c_remarks = str(row_dict.get('remarks') or '').strip()

        # 12. Closing Date & GR Date
        c_closing = cls._parse_date(row_dict.get('closing_date')) if row_dict.get('closing_date') else ''
        if row_dict.get('closing_date') and not c_closing:
            errors.append(f"Closing Date '{row_dict.get('closing_date')}' is invalid (use YYYY-MM-DD).")
        elif c_closing and c_date and c_closing < c_date:
            errors.append(f"Closing Date ({c_closing}) cannot be earlier than Complain Date ({c_date}).")

        c_gr_date = cls._parse_date(row_dict.get('gr_expected_date')) if row_dict.get('gr_expected_date') else ''
        if row_dict.get('gr_expected_date') and not c_gr_date:
            errors.append(f"GR Expected Date '{row_dict.get('gr_expected_date')}' is invalid (use YYYY-MM-DD).")

        # 13. GR Amount
        raw_gr = str(row_dict.get('gr_amount') or '').strip()
        gr_val_str = ''
        if raw_gr:
            try:
                gr_val = float(raw_gr.replace(',', ''))
                if gr_val < 0:
                    errors.append("GR Amount cannot be negative.")
                gr_val_str = f"{gr_val:.2f}"
            except ValueError:
                errors.append(f"GR Amount '{raw_gr}' must be numeric.")

        # 14. True Duplicate Protection (Allow duplicate entries until EVERY SINGLE THING is identical)
        row_record_id = str(row_dict.get('record_id') or row_dict.get('complaint_id') or '').strip()
        if row_record_id.lower() in ('-', 'n/a', 'na', 'none', 'null'):
            row_record_id = ''

        if row_record_id:
            rec_upper = row_record_id.upper()
            if rec_upper in in_file_record_ids:
                errors.append(f"Duplicate Record ID '{row_record_id}' in uploaded file (Row {r_num}).")
            elif rec_upper in existing_complaint_ids or rec_upper in existing_record_ids:
                errors.append(f"Duplicate complaint: Record ID '{row_record_id}' already exists in database (cannot be re-imported).")
            else:
                in_file_record_ids.add(rec_upper)
        else:
            # 100% Identical Duplicate Check (Blocked ONLY when every single field is identical)
            mob_digits = re.sub(r'\D', '', mob_val)
            if len(mob_digits) > 10:
                mob_digits = mob_digits[-10:]

            cat_clean = str(row_dict.get('category') or '').strip().lower()
            vendor_clean = str(row_dict.get('vendor_name') or '').strip().lower()
            handler_clean = str(row_dict.get('handled_by') or '').strip().lower()
            sol_clean = str(row_dict.get('solution_given') or '').strip().lower()
            gr_exp_clean = str(c_gr_date or '').strip()

            exact_fingerprint = (
                f"{c_name_display.lower()}|{mob_digits}|{date_val}|{c_type.lower()}|"
                f"{canon_dept.lower()}|{canon_from.lower()}|{c_inv.lower()}|"
                f"{cat_clean}|{vendor_clean}|{canon_sp.lower()}|{c_details.lower()}|"
                f"{c_remarks.lower()}|{canon_prio.lower()}|{canon_status.lower()}|"
                f"{handler_clean}|{gr_val_str}|{c_closing or ''}|{sol_clean}|{gr_exp_clean}"
            )
            if exact_fingerprint in in_file_fingerprints:
                errors.append("Duplicate complaint in uploaded file: An exact 100% identical complaint record already exists in this batch.")
            elif exact_fingerprint in existing_record_fingerprints:
                errors.append("Duplicate complaint: An exact 100% identical complaint record already exists in database (accidental re-import blocked).")
            else:
                in_file_fingerprints.add(exact_fingerprint)

        return {
            'row_number': r_num,
            'customer_name': c_name_display,
            'customer_mobile': mob_val,
            'complain_date': date_val,
            'complain_types': c_type,
            'department_name': canon_dept,
            'from_department': canon_from,
            'invoice_no': c_inv,
            'priority': canon_prio,
            'status': canon_status,
            'category': str(row_dict.get('category') or '').strip(),
            'vendor_name': str(row_dict.get('vendor_name') or '').strip(),
            'salesperson_name': canon_sp,
            'handled_by': str(row_dict.get('handled_by') or '').strip(),
            'remarks': c_remarks,
            'complain_details': c_details,
            'closing_date': c_closing or '',
            'solution_given': str(row_dict.get('solution_given') or '').strip(),
            'gr_amount': gr_val_str,
            'gr_expected_date': c_gr_date or '',
            'record_id': row_record_id,
            'errors': errors
        }

    @classmethod
    def parse_and_validate(cls, file_stream, current_user: Dict[str, Any], db_engine=None) -> Dict[str, Any]:
        """
        Parses uploaded Excel workbook and validates each row using smart rules.
        """
        _cleanup_preview_cache()

        if not current_user:
            raise ValueError("User authentication required for Excel import.")

        try:
            wb = openpyxl.load_workbook(file_stream, data_only=True)
        except Exception as e:
            raise ValueError(f"Unable to parse Excel file. Please ensure it is a valid .xlsx file. Error: {e}")

        # Find target worksheet
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

        # Map headers
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

        required_keys = {'customer_name', 'customer_mobile', 'complain_date', 'complain_types', 'department_name'}
        mapped_keys = set(col_to_key.values())
        missing_required = required_keys - mapped_keys
        if missing_required:
            raise ValueError(f"Missing mandatory columns in Excel header: {', '.join(sorted(missing_required))}. Please use the official template.")

        # Load reference master data
        active_dept_map, active_types_map, active_salespersons_map, existing_complaint_ids, existing_record_ids, existing_record_fingerprints = cls._load_master_data(db_engine)

        valid_rows: List[Dict[str, Any]] = []
        invalid_rows: List[Dict[str, Any]] = []
        in_file_record_ids = set()
        in_file_fingerprints = set()

        sample_name_patterns = {'anjali gupta', 'sneha rao', 'sample customer', 'customer name'}

        for r_num, row in enumerate(rows[1:], start=2):
            if not row or all(v is None or str(v).strip() == '' for v in row):
                continue

            row_dict: Dict[str, Any] = {}
            for col_idx, key in col_to_key.items():
                val = row[col_idx] if col_idx < len(row) else None
                row_dict[key] = val

            cust_name_check = str(row_dict.get('customer_name') or '').strip().lower()
            if cust_name_check in sample_name_patterns and r_num in (2, 3):
                continue

            summary = cls.validate_single_row(
                row_dict=row_dict,
                r_num=r_num,
                current_user=current_user,
                active_dept_map=active_dept_map,
                active_types_map=active_types_map,
                active_salespersons_map=active_salespersons_map,
                existing_complaint_ids=existing_complaint_ids,
                existing_record_ids=existing_record_ids,
                existing_record_fingerprints=existing_record_fingerprints,
                in_file_record_ids=in_file_record_ids,
                in_file_fingerprints=in_file_fingerprints
            )

            if summary['errors']:
                invalid_rows.append(summary)
            else:
                valid_rows.append(summary)

        preview_token = uuid.uuid4().hex
        _PREVIEW_CACHE[preview_token] = {
            'valid_rows': valid_rows,
            'invalid_rows': invalid_rows,
            'user_id': current_user.get('user_id'),
            'user_email': current_user.get('email'),
            'timestamp': time.time()
        }

        master_options = cls.get_master_options(db_engine)

        return {
            'total_rows': len(valid_rows) + len(invalid_rows),
            'valid_count': len(valid_rows),
            'invalid_count': len(invalid_rows),
            'valid_rows': valid_rows,
            'invalid_rows': invalid_rows,
            'preview_token': preview_token,
            'master_options': master_options
        }

    @classmethod
    def revalidate_batch(cls, preview_token: str, rows_payload: List[Dict[str, Any]], current_user: Dict[str, Any], db_engine=None) -> Dict[str, Any]:
        """
        Revalidates an updated batch of complaint rows from the frontend editable preview.
        Updates cache and returns updated counts and validation states.
        """
        _cleanup_preview_cache()
        preview_data = _PREVIEW_CACHE.get(preview_token)
        if not preview_data:
            raise ValueError("Preview session has expired. Please upload the file again.")

        if str(preview_data.get('user_id', '')) != str(current_user.get('user_id', '')):
            raise PermissionError("Unauthorized preview session.")

        active_dept_map, active_types_map, active_salespersons_map, existing_complaint_ids, existing_record_ids, existing_record_fingerprints = cls._load_master_data(db_engine)

        valid_rows: List[Dict[str, Any]] = []
        invalid_rows: List[Dict[str, Any]] = []
        in_file_record_ids = set()
        in_file_fingerprints = set()

        for idx, row_dict in enumerate(rows_payload, start=1):
            r_num = row_dict.get('row_number', idx)
            summary = cls.validate_single_row(
                row_dict=row_dict,
                r_num=r_num,
                current_user=current_user,
                active_dept_map=active_dept_map,
                active_types_map=active_types_map,
                active_salespersons_map=active_salespersons_map,
                existing_complaint_ids=existing_complaint_ids,
                existing_record_ids=existing_record_ids,
                existing_record_fingerprints=existing_record_fingerprints,
                in_file_record_ids=in_file_record_ids,
                in_file_fingerprints=in_file_fingerprints
            )

            if summary['errors']:
                invalid_rows.append(summary)
            else:
                valid_rows.append(summary)

        # Update cache
        preview_data['valid_rows'] = valid_rows
        preview_data['invalid_rows'] = invalid_rows
        preview_data['timestamp'] = time.time()

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
                row.get('gr_expected_date', ''),
                row.get('record_id', '')
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

        # Auto-fit column widths
        ws.column_dimensions['A'].width = 8
        ws.column_dimensions['B'].width = 45
        for col_idx in range(3, len(headers) + 1):
            letter = get_column_letter(col_idx)
            ws.column_dimensions[letter].width = 18

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf
