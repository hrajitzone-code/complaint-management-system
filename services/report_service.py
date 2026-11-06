"""
Complaint Management System - Advanced Reports & Analytics Service
Provides dynamic query filtering, multi-field aggregations, RBAC enforcement,
and multi-format report exports (Excel .xlsx, CSV, PDF, and Printable data).
"""

import io
import csv
import re
import datetime
import logging
from collections import defaultdict

# Third-party export libraries (verified pre-installed in environment)
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak
)
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

# Canonical Fields Definition
ALL_REPORT_COLUMNS = [
    {'id': 'Complaint ID', 'label': 'Complaint ID', 'category': 'Identity', 'default': True},
    {'id': 'Complain Date', 'label': 'Complain Date', 'category': 'Timeline', 'default': True},
    {'id': 'Customer Name', 'label': 'Customer Name', 'category': 'Customer', 'default': True},
    {'id': 'Customer Mobile No.', 'label': 'Customer Mobile', 'category': 'Customer', 'default': False},
    {'id': 'Employee Name', 'label': 'Employee Name', 'category': 'Staff', 'default': False},
    {'id': 'Salesperson Name', 'label': 'Salesperson', 'category': 'Staff', 'default': False},
    {'id': 'Department Name', 'label': 'Department', 'category': 'Classification', 'default': True},
    {'id': 'Complain Types', 'label': 'Complain Types', 'category': 'Classification', 'default': True},
    {'id': 'Category', 'label': 'Category', 'category': 'Classification', 'default': False},
    {'id': 'Vendor Name', 'label': 'Vendor Name', 'category': 'Classification', 'default': True},
    {'id': 'Priority', 'label': 'Priority', 'category': 'Status & Priority', 'default': True},
    {'id': 'Status', 'label': 'Status', 'category': 'Status & Priority', 'default': True},
    {'id': 'Invoice No.', 'label': 'Invoice No.', 'category': 'Customer', 'default': True},
    {'id': 'Complain Details', 'label': 'Complain Details', 'category': 'Issue Details', 'default': False},
    {'id': 'Remarks', 'label': 'Remarks', 'category': 'Issue Details', 'default': False},
    {'id': 'Handled By', 'label': 'Handled By', 'category': 'Resolution', 'default': True},
    {'id': 'Closing Date', 'label': 'Closing Date', 'category': 'Resolution', 'default': False},
    {'id': 'Solution Given', 'label': 'Solution Given', 'category': 'Resolution', 'default': False},
    {'id': 'Total Time Taken', 'label': 'Total Time Taken', 'category': 'Resolution', 'default': True},
    {'id': 'GR Amount', 'label': 'GR Amount (₹)', 'category': 'Goods Return', 'default': True},
    {'id': 'GR Expected Date', 'label': 'GR Expected Date', 'category': 'Goods Return', 'default': False},
    {'id': 'Comments', 'label': 'Comments', 'category': 'Resolution', 'default': False},
    {'id': 'Attachment Link', 'label': 'Attachment', 'category': 'Identity', 'default': False},
    {'id': 'Complainant Email', 'label': 'Complainant Email', 'category': 'Customer', 'default': False},
    {'id': 'Timestamp', 'label': 'Created At', 'category': 'Timeline', 'default': False},
    {'id': 'Last Updated At', 'label': 'Last Updated', 'category': 'Timeline', 'default': False}
]

COLUMN_MAP = {col['id']: col for col in ALL_REPORT_COLUMNS}
DEFAULT_COL_IDS = [col['id'] for col in ALL_REPORT_COLUMNS if col['default']]


# NumberedCanvas for PDF multi-page numbering
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        # Landscape footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(11 * inch - 0.5 * inch, 0.35 * inch, page_text)
        gen_text = f"Customer Complaint Management System — Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        self.drawString(0.5 * inch, 0.35 * inch, gen_text)
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(0.5 * inch, 0.5 * inch, 11 * inch - 0.5 * inch, 0.5 * inch)
        self.restoreState()


class ReportService:
    """
    Central report generation, analytical aggregation, and export engine.
    Directly operates on Google Sheets complaint records.
    """

    @classmethod
    def parse_float(cls, val, default=0.0):
        """Safely parses monetary or numeric values."""
        if val is None:
            return default
        try:
            # Strip currency symbols, commas, spaces
            cleaned = re.sub(r'[^\d.-]', '', str(val)).strip()
            if not cleaned or cleaned == '-' or cleaned == '.':
                return default
            return float(cleaned)
        except Exception:
            return default

    @classmethod
    def parse_date(cls, val):
        """Attempts to parse standard date formats."""
        if not val:
            return None
        val_str = str(val).strip()
        for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.datetime.strptime(val_str.split(' ')[0], fmt.split(' ')[0]).date()
            except Exception:
                continue
        return None

    @classmethod
    def calculate_resolution_days(cls, comp):
        """
        Calculates resolution duration in days.
        Uses Complain Date and Closing Date, or parses Total Time Taken, or elapsed days.
        """
        c_date = cls.parse_date(comp.get('Complain Date'))
        close_date = cls.parse_date(comp.get('Closing Date') or comp.get('Resolved At'))

        if c_date and close_date and close_date >= c_date:
            return max(0, (close_date - c_date).days)

        # Try parsing from "X days" text
        tt = comp.get('Total Time Taken', '')
        if tt:
            m = re.search(r'(\d+)', str(tt))
            if m:
                return int(m.group(1))

        # If not closed, calculate elapsed days from complain date to today
        if c_date and comp.get('Status') not in ('Resolved', 'Closed'):
            today = datetime.datetime.now(datetime.timezone.utc).date()
            if today >= c_date:
                return max(0, (today - c_date).days)

        return 0

    @classmethod
    def is_complaint_overdue(cls, comp):
        """
        Determines whether a complaint is overdue.
        Conditions:
        - Status is NOT Resolved or Closed.
        - AND either:
          a) Age > 7 days, OR
          b) GR Expected Date is past today.
        """
        status = comp.get('Status', 'Submitted')
        if status in ('Resolved', 'Closed', 'Rejected'):
            return False

        today = datetime.datetime.now(datetime.timezone.utc).date()

        gr_exp = cls.parse_date(comp.get('GR Expected Date'))
        if gr_exp and gr_exp < today:
            return True

        c_date = cls.parse_date(comp.get('Complain Date'))
        if c_date:
            age = (today - c_date).days
            if age > 7:
                return True

        return False

    @classmethod
    def filter_by_rbac(cls, complaints, user):
        """
        Strict Role-Based Access Control filtering:
        - Super Admin & Admin: Access all records across all departments.
        - Support Staff: Access records belonging to their assigned department OR assigned to them.
        - User: Strictly access ONLY their own submitted records (Complainant Email == user email).
        """
        if not user:
            return []

        role = user.get('role', 'User')
        user_email = (user.get('email') or '').lower().strip()
        user_name = (user.get('name') or '').lower().strip()
        user_dept = (user.get('department') or '').lower().strip()

        if role in ('Super Admin', 'Admin'):
            return list(complaints)

        if role == 'Support Staff':
            allowed = []
            for c in complaints:
                # 1. Match Handled By name / email
                handled_by = (c.get('Handled By') or '').lower().strip()
                if handled_by and (user_name in handled_by or user_email in handled_by):
                    allowed.append(c)
                    continue

                # 2. Match department assignment (supporting multi-department values)
                c_dept_raw = (c.get('Department Name') or c.get('Department') or '').lower()
                c_depts = [p.strip() for p in c_dept_raw.split(',') if p.strip()]
                if user_dept and (user_dept in c_depts or any(user_dept in d for d in c_depts)):
                    allowed.append(c)
                    continue

                # 3. Match own complaints submitted by this staff member
                if c.get('Complainant Email', '').lower().strip() == user_email:
                    allowed.append(c)
                    continue

            return allowed

        # Regular user: strictly own complaints
        return [
            c for c in complaints
            if c.get('Complainant Email', '').lower().strip() == user_email
        ]

    @classmethod
    def apply_filters(cls, complaints, filters):
        """
        Applies dynamic multi-field filters to complaint records.
        """
        if not filters:
            return list(complaints)

        filtered = []
        today = datetime.datetime.now(datetime.timezone.utc).date()

        # 1. Date Range Filter
        date_preset = (filters.get('date_preset') or 'all').lower().strip()
        start_date = cls.parse_date(filters.get('start_date'))
        end_date = cls.parse_date(filters.get('end_date'))

        if date_preset == 'today':
            start_date = today
            end_date = today
        elif date_preset == 'yesterday':
            yesterday = today - datetime.timedelta(days=1)
            start_date = yesterday
            end_date = yesterday
        elif date_preset == 'this_week':
            # Monday of current week
            start_date = today - datetime.timedelta(days=today.weekday())
            end_date = start_date + datetime.timedelta(days=6)
        elif date_preset == 'this_month':
            start_date = today.replace(day=1)
            next_month = (start_date.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            end_date = next_month - datetime.timedelta(days=1)
        elif date_preset == 'last_month':
            first_this_month = today.replace(day=1)
            last_day_last_month = first_this_month - datetime.timedelta(days=1)
            start_date = last_day_last_month.replace(day=1)
            end_date = last_day_last_month
        elif date_preset == 'this_quarter':
            q_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=q_month, day=1)
            end_q_month = q_month + 2
            next_m = (today.replace(month=end_q_month, day=28) + datetime.timedelta(days=4)).replace(day=1)
            end_date = next_m - datetime.timedelta(days=1)
        elif date_preset == 'this_year':
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

        # 2. Multi-Department Filter
        selected_depts = filters.get('departments')
        if isinstance(selected_depts, str):
            selected_depts = [p.strip().lower() for p in selected_depts.split(',') if p.strip()]
        elif isinstance(selected_depts, (list, tuple)):
            selected_depts = [str(p).strip().lower() for p in selected_depts if str(p).strip()]
        else:
            selected_depts = []

        # 3. Multi-Vendor Filter
        selected_vendors = filters.get('vendors')
        if isinstance(selected_vendors, str):
            selected_vendors = [p.strip().lower() for p in selected_vendors.split(',') if p.strip()]
        elif isinstance(selected_vendors, (list, tuple)):
            selected_vendors = [str(p).strip().lower() for p in selected_vendors if str(p).strip()]
        else:
            selected_vendors = []

        # 4. Status Filter
        selected_statuses = filters.get('statuses')
        if isinstance(selected_statuses, str):
            selected_statuses = [p.strip().lower() for p in selected_statuses.split(',') if p.strip()]
        elif isinstance(selected_statuses, (list, tuple)):
            selected_statuses = [str(p).strip().lower() for p in selected_statuses if str(p).strip()]
        else:
            selected_statuses = []

        # 5. Priority Filter
        selected_priorities = filters.get('priorities')
        if isinstance(selected_priorities, str):
            selected_priorities = [p.strip().lower() for p in selected_priorities.split(',') if p.strip()]
        elif isinstance(selected_priorities, (list, tuple)):
            selected_priorities = [str(p).strip().lower() for p in selected_priorities if str(p).strip()]
        else:
            selected_priorities = []

        # Text search filters
        emp_filter = (filters.get('employee_name') or '').lower().strip()
        handled_by_filter = (filters.get('handled_by') or '').lower().strip()
        salesperson_filter = (filters.get('salesperson_name') or '').lower().strip()
        type_filter = (filters.get('complain_types') or '').lower().strip()
        category_filter = (filters.get('category') or '').lower().strip()
        customer_filter = (filters.get('customer_name') or '').lower().strip()
        mobile_filter = (filters.get('customer_mobile_no') or '').strip()
        invoice_filter = (filters.get('invoice_no') or '').lower().strip()
        global_search = (filters.get('search') or '').lower().strip()

        # Dates & Numeric Ranges
        closing_start = cls.parse_date(filters.get('closing_start_date'))
        closing_end = cls.parse_date(filters.get('closing_end_date'))
        gr_exp_start = cls.parse_date(filters.get('gr_expected_start_date'))
        gr_exp_end = cls.parse_date(filters.get('gr_expected_end_date'))

        gr_min = filters.get('gr_amount_min')
        gr_max = filters.get('gr_amount_max')
        gr_min = cls.parse_float(gr_min) if (gr_min is not None and str(gr_min).strip() != '') else None
        gr_max = cls.parse_float(gr_max) if (gr_max is not None and str(gr_max).strip() != '') else None

        overdue_only = str(filters.get('overdue_only', '')).lower() in ('true', '1', 'yes')

        # Filter iteration
        for c in complaints:
            # Date filter
            if start_date or end_date:
                comp_dt = cls.parse_date(c.get('Complain Date'))
                if not comp_dt:
                    continue
                if start_date and comp_dt < start_date:
                    continue
                if end_date and comp_dt > end_date:
                    continue

            # Departments filter (checks multi-department comma strings)
            if selected_depts:
                c_dept_raw = (c.get('Department Name') or c.get('Department') or '').lower()
                c_depts = [p.strip() for p in c_dept_raw.split(',') if p.strip()]
                # Match if any selected department is tagged in this complaint
                if not any(sd in c_depts or any(sd in cd for cd in c_depts) for sd in selected_depts):
                    continue

            # Vendors filter
            if selected_vendors:
                v_name = (c.get('Vendor Name') or '').lower().strip()
                if not any(sv in v_name for sv in selected_vendors):
                    continue

            # Status filter
            if selected_statuses:
                c_status = (c.get('Status') or 'Submitted').lower().strip()
                if c_status not in selected_statuses:
                    continue

            # Priority filter
            if selected_priorities:
                c_prio = (c.get('Priority') or 'Medium').lower().strip()
                if c_prio not in selected_priorities:
                    continue

            # Text filters
            if emp_filter and emp_filter not in (c.get('Employee Name') or '').lower():
                continue
            if handled_by_filter and handled_by_filter not in (c.get('Handled By') or '').lower():
                continue
            if salesperson_filter and salesperson_filter not in (c.get('Salesperson Name') or '').lower():
                continue
            if type_filter and type_filter not in (c.get('Complain Types') or '').lower():
                continue
            if category_filter and category_filter not in (c.get('Category') or '').lower():
                continue
            if customer_filter and customer_filter not in (c.get('Customer Name') or '').lower():
                continue
            if mobile_filter and mobile_filter not in str(c.get('Customer Mobile No.') or ''):
                continue
            if invoice_filter and invoice_filter not in (c.get('Invoice No.') or '').lower():
                continue

            # Closing Date
            if closing_start or closing_end:
                c_close = cls.parse_date(c.get('Closing Date') or c.get('Resolved At'))
                if not c_close:
                    continue
                if closing_start and c_close < closing_start:
                    continue
                if closing_end and c_close > closing_end:
                    continue

            # GR Expected Date
            if gr_exp_start or gr_exp_end:
                c_gr_exp = cls.parse_date(c.get('GR Expected Date'))
                if not c_gr_exp:
                    continue
                if gr_exp_start and c_gr_exp < gr_exp_start:
                    continue
                if gr_exp_end and c_gr_exp > gr_exp_end:
                    continue

            # GR Amount range
            c_gr_val = cls.parse_float(c.get('GR Amount'))
            if gr_min is not None and c_gr_val < gr_min:
                continue
            if gr_max is not None and c_gr_val > gr_max:
                continue

            # Overdue toggle
            if overdue_only and not cls.is_complaint_overdue(c):
                continue

            # Global keyword search
            if global_search:
                search_corpus = " ".join([
                    c.get('Complaint ID', ''),
                    c.get('Customer Name', ''),
                    c.get('Customer Mobile No.', ''),
                    c.get('Employee Name', ''),
                    c.get('Invoice No.', ''),
                    c.get('Complain Types', ''),
                    c.get('Complain Details', ''),
                    c.get('Remarks', ''),
                    c.get('Department Name', ''),
                    c.get('Category', ''),
                    c.get('Vendor Name', ''),
                    c.get('Salesperson Name', ''),
                    c.get('Handled By', ''),
                    c.get('Status', '')
                ]).lower()
                if global_search not in search_corpus:
                    continue

            filtered.append(c)

        return filtered

    @classmethod
    def calculate_aggregations(cls, filtered_records):
        """
        Calculates comprehensive analytical KPIs, distributions, and trends.
        Multi-department complaints are attributed cleanly:
        - Overall complaint count is exact and unique.
        - Department breakdown counts complaints tagged with each department.
        """
        total_count = len(filtered_records)
        status_counts = defaultdict(int)
        priority_counts = defaultdict(int)
        dept_counts = defaultdict(int)
        vendor_counts = defaultdict(lambda: {'count': 0, 'gr_amount': 0.0})
        employee_stats = defaultdict(lambda: {'handled': 0, 'resolved': 0, 'total_days': 0})
        monthly_trend = defaultdict(lambda: {'lodged': 0, 'resolved': 0})

        total_gr_amount = 0.0
        pending_gr_amount = 0.0
        overdue_count = 0
        total_resolution_days = 0
        resolved_with_days = 0

        for c in filtered_records:
            status = (c.get('Status') or 'Submitted').strip()
            priority = (c.get('Priority') or 'Medium').strip().capitalize()
            gr_val = cls.parse_float(c.get('GR Amount'))
            res_days = cls.calculate_resolution_days(c)
            is_overdue = cls.is_complaint_overdue(c)

            status_counts[status] += 1
            priority_counts[priority] += 1
            total_gr_amount += gr_val

            if status not in ('Resolved', 'Closed', 'Rejected'):
                pending_gr_amount += gr_val
            else:
                total_resolution_days += res_days
                resolved_with_days += 1

            if is_overdue:
                overdue_count += 1

            # Multi-department attribution
            raw_dept = c.get('Department Name') or c.get('Department') or 'General'
            dept_parts = [p.strip() for p in raw_dept.split(',') if p.strip()]
            if not dept_parts:
                dept_parts = ['General']
            for d in dept_parts:
                dept_counts[d] += 1

            # Vendor breakdown
            vendor = (c.get('Vendor Name') or 'Unspecified').strip()
            vendor_counts[vendor]['count'] += 1
            vendor_counts[vendor]['gr_amount'] += gr_val

            # Handled By / Employee stats
            handler = (c.get('Handled By') or 'Unassigned').strip()
            employee_stats[handler]['handled'] += 1
            if status in ('Resolved', 'Closed'):
                employee_stats[handler]['resolved'] += 1
                employee_stats[handler]['total_days'] += res_days

            # Monthly Trend
            comp_dt = cls.parse_date(c.get('Complain Date'))
            if comp_dt:
                m_key = comp_dt.strftime('%Y-%m')
                monthly_trend[m_key]['lodged'] += 1
            close_dt = cls.parse_date(c.get('Closing Date') or c.get('Resolved At'))
            if close_dt:
                m_key = close_dt.strftime('%Y-%m')
                monthly_trend[m_key]['resolved'] += 1

        resolved_count = status_counts.get('Resolved', 0) + status_counts.get('Closed', 0)
        pending_count = (
            status_counts.get('Submitted', 0) +
            status_counts.get('Under Review', 0) +
            status_counts.get('In Progress', 0)
        )
        resolution_rate = round((resolved_count / total_count * 100), 1) if total_count > 0 else 0.0
        avg_res_time = round((total_resolution_days / resolved_with_days), 1) if resolved_with_days > 0 else 0.0

        # Format sorted department breakdown
        dept_breakdown = []
        for d, count in sorted(dept_counts.items(), key=lambda x: x[1], reverse=True):
            pct = round((count / total_count * 100), 1) if total_count > 0 else 0.0
            dept_breakdown.append({
                'department': d,
                'count': count,
                'percentage': pct
            })

        # Format top vendors
        top_vendors = []
        for v, stats in sorted(vendor_counts.items(), key=lambda x: x[1]['count'], reverse=True)[:10]:
            top_vendors.append({
                'vendor': v,
                'count': stats['count'],
                'gr_amount': round(stats['gr_amount'], 2)
            })

        # Format monthly trend chronologically
        trend_list = []
        for m_key in sorted(monthly_trend.keys()):
            trend_list.append({
                'month': m_key,
                'lodged': monthly_trend[m_key]['lodged'],
                'resolved': monthly_trend[m_key]['resolved']
            })

        # Format employee performance
        employee_performance = []
        for handler, st in sorted(employee_stats.items(), key=lambda x: x[1]['handled'], reverse=True):
            h_rate = round((st['resolved'] / st['handled'] * 100), 1) if st['handled'] > 0 else 0.0
            h_avg = round((st['total_days'] / st['resolved']), 1) if st['resolved'] > 0 else 0.0
            employee_performance.append({
                'employee': handler,
                'handled': st['handled'],
                'resolved': st['resolved'],
                'resolution_rate': h_rate,
                'avg_time_days': h_avg
            })

        return {
            'kpis': {
                'total_complaints': total_count,
                'resolved_count': resolved_count,
                'pending_count': pending_count,
                'overdue_count': overdue_count,
                'resolution_rate': resolution_rate,
                'avg_resolution_time_days': avg_res_time,
                'total_gr_amount': round(total_gr_amount, 2),
                'pending_gr_amount': round(pending_gr_amount, 2)
            },
            'status_breakdown': dict(status_counts),
            'priority_breakdown': dict(priority_counts),
            'department_breakdown': dept_breakdown,
            'top_vendors': top_vendors,
            'monthly_trend': trend_list,
            'employee_performance': employee_performance
        }

    @classmethod
    def sort_and_paginate(cls, records, sort_by='Complain Date', sort_dir='desc', page=1, page_size=25):
        """
        Sorts and paginates complaint records.
        """
        reverse = (str(sort_dir).lower() == 'desc')

        def sort_key(rec):
            val = rec.get(sort_by, '')
            if sort_by in ('GR Amount',):
                return cls.parse_float(val)
            if sort_by in ('Complain Date', 'Closing Date', 'GR Expected Date', 'Timestamp', 'Last Updated At'):
                dt = cls.parse_date(val)
                return dt or datetime.date.min
            return str(val).lower()

        sorted_records = sorted(records, key=sort_key, reverse=reverse)

        total_records = len(sorted_records)
        if str(page_size).lower() == 'all' or int(page_size) <= 0:
            return sorted_records, 1, 1, total_records

        page_size = int(page_size)
        total_pages = max(1, (total_records + page_size - 1) // page_size)
        page = max(1, min(int(page), total_pages))

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paged_records = sorted_records[start_idx:end_idx]

        return paged_records, page, total_pages, total_records

    # =========================================================================
    # EXPORT GENERATORS: EXCEL (.xlsx), CSV, PDF (.pdf)
    # =========================================================================

    @classmethod
    def generate_excel(cls, records, columns, report_title="Complaint Management Report", filter_summary=None, user_name="User"):
        """
        Generates a professionally styled Microsoft Excel (.xlsx) file.
        Includes metadata header, active filters, selected columns,
        alternating rows, formatted monetary amounts, and bottom totals.
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Complaints Report"
        ws.views.sheetView[0].showGridLines = True

        # Styles
        font_title = Font(name="Segoe UI", size=15, bold=True, color="1E3A8A")
        font_meta = Font(name="Segoe UI", size=9, color="475569")
        font_header = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        font_data = Font(name="Segoe UI", size=9, color="0F172A")
        font_total = Font(name="Segoe UI", size=10, bold=True, color="0F172A")

        fill_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        fill_subtotal = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        fill_alt_row = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        border_thin = Side(style="thin", color="CBD5E1")
        border_cell = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)
        border_total = Border(top=Side(style="thin", color="0F172A"), bottom=Side(style="double", color="0F172A"))

        align_left = Alignment(horizontal="left", vertical="center")
        align_center = Alignment(horizontal="center", vertical="center")
        align_right = Alignment(horizontal="right", vertical="center")

        num_cols = len(columns)

        # 1. Report Title & Generation Metadata
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(4, num_cols))
        ws.cell(row=1, column=1, value=report_title.upper()).font = font_title
        ws.row_dimensions[1].height = 28

        gen_time_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        meta_line1 = f"Generated By: {user_name}  |  Generated At: {gen_time_str}  |  Total Records: {len(records)}"
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(4, num_cols))
        ws.cell(row=2, column=1, value=meta_line1).font = font_meta

        filter_desc = filter_summary or "Active Filters: All Records"
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=max(4, num_cols))
        ws.cell(row=3, column=1, value=f"Applied Filters: {filter_desc}").font = font_meta
        ws.row_dimensions[3].height = 18

        # 2. Table Column Headers
        header_row = 5
        ws.row_dimensions[header_row].height = 24

        col_keys = [c['id'] for c in columns]
        col_labels = [c['label'] for c in columns]

        for col_idx, label in enumerate(col_labels, start=1):
            cell = ws.cell(row=header_row, column=col_idx, value=label)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_center
            cell.border = border_cell

        # 3. Data Rows
        current_row = header_row + 1
        total_gr = 0.0

        for r_idx, rec in enumerate(records):
            ws.row_dimensions[current_row].height = 20
            is_alt = (r_idx % 2 == 1)

            for col_idx, col_id in enumerate(col_keys, start=1):
                val = rec.get(col_id, '')
                cell = ws.cell(row=current_row, column=col_idx)
                cell.font = font_data
                cell.border = border_cell
                if is_alt:
                    cell.fill = fill_alt_row

                # Format specific column types
                if col_id == 'GR Amount':
                    num_val = cls.parse_float(val)
                    total_gr += num_val
                    cell.value = num_val
                    cell.number_format = '₹#,##0.00'
                    cell.alignment = align_right
                elif col_id in ('Complain Date', 'Closing Date', 'GR Expected Date'):
                    cell.value = str(val) if val else '-'
                    cell.alignment = align_center
                elif col_id in ('Complaint ID', 'Priority', 'Status', 'Invoice No.'):
                    cell.value = str(val)
                    cell.alignment = align_center
                else:
                    cell.value = str(val)
                    cell.alignment = align_left

            current_row += 1

        # 4. Totals Summary Row
        ws.row_dimensions[current_row].height = 22
        first_cell = ws.cell(row=current_row, column=1, value=f"Total Records: {len(records)}")
        first_cell.font = font_total
        first_cell.fill = fill_subtotal
        first_cell.border = border_total
        first_cell.alignment = align_left

        for col_idx, col_id in enumerate(col_keys[1:], start=2):
            cell = ws.cell(row=current_row, column=col_idx)
            cell.font = font_total
            cell.fill = fill_subtotal
            cell.border = border_total
            if col_id == 'GR Amount':
                cell.value = total_gr
                cell.number_format = '₹#,##0.00'
                cell.alignment = align_right
            else:
                cell.value = ''

        # 5. Column Width Auto-Fitting
        for col_idx, col_id in enumerate(col_keys, start=1):
            col_letter = get_column_letter(col_idx)
            max_len = len(col_labels[col_idx - 1])
            for r in range(header_row, current_row + 1):
                val_str = str(ws.cell(row=r, column=col_idx).value or '')
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = min(40, max(12, max_len + 3))

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()

    @classmethod
    def generate_csv(cls, records, columns, report_title="Complaint Management Report", filter_summary=None, user_name="User"):
        """
        Generates a clean CSV file with UTF-8 BOM encoding for complete Excel compatibility.
        Includes report metadata comments at top.
        """
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator='\r\n')

        # Metadata Comments
        writer.writerow([f"# {report_title}"])
        writer.writerow([f"# Generated By: {user_name} | Generated At: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
        writer.writerow([f"# Filters: {filter_summary or 'All Records'}"])
        writer.writerow([f"# Total Records: {len(records)}"])
        writer.writerow([])

        # Header Row
        col_keys = [c['id'] for c in columns]
        col_labels = [c['label'] for c in columns]
        writer.writerow(col_labels)

        # Data Rows
        for r in records:
            row_vals = [r.get(cid, '') for cid in col_keys]
            writer.writerow(row_vals)

        # Add UTF-8 Byte Order Mark for proper Excel UTF-8 recognition
        raw_bytes = buf.getvalue().encode('utf-8-sig')
        return raw_bytes

    @classmethod
    def generate_pdf(cls, records, columns, report_title="Complaint Management Report", filter_summary=None, user_name="User", kpis=None):
        """
        Generates a publication-grade PDF document in landscape orientation.
        Features responsive table cells, styled headers, KPI summary boxes, and page numbering.
        """
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=landscape(letter),
            leftMargin=0.4 * inch,
            rightMargin=0.4 * inch,
            topMargin=0.5 * inch,
            bottomMargin=0.6 * inch
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'ReportTitle',
            parent=styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=16,
            leading=20,
            textColor=colors.HexColor('#1e3a8a'),
            spaceAfter=4
        )

        meta_style = ParagraphStyle(
            'ReportMeta',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#475569')
        )

        kpi_title_style = ParagraphStyle(
            'KPITitle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=7,
            textColor=colors.HexColor('#64748b'),
            alignment=1
        )

        kpi_val_style = ParagraphStyle(
            'KPIVal',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=12,
            textColor=colors.HexColor('#0f172a'),
            alignment=1
        )

        th_style = ParagraphStyle(
            'TableHeader',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=7.5,
            leading=9,
            textColor=colors.white,
            alignment=1
        )

        td_style = ParagraphStyle(
            'TableCell',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=7,
            leading=9,
            textColor=colors.HexColor('#1e293b')
        )

        story = []

        # 1. Document Title
        story.append(Paragraph(report_title, title_style))

        # 2. Generation Metadata
        gen_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        meta_p = (
            f"<b>Generated By:</b> {user_name} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Date/Time:</b> {gen_time} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Filters:</b> {filter_summary or 'All Records'} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Total Records:</b> {len(records)}"
        )
        story.append(Paragraph(meta_p, meta_style))
        story.append(Spacer(1, 8))

        # 3. KPI Summary Table if provided
        if kpis:
            kpi_data = [
                [
                    Paragraph("TOTAL COMPLAINTS", kpi_title_style),
                    Paragraph("RESOLVED / CLOSED", kpi_title_style),
                    Paragraph("PENDING / IN PROGRESS", kpi_title_style),
                    Paragraph("OVERDUE", kpi_title_style),
                    Paragraph("RESOLUTION RATE", kpi_title_style),
                    Paragraph("TOTAL GR AMOUNT", kpi_title_style),
                ],
                [
                    Paragraph(str(kpis.get('total_complaints', len(records))), kpi_val_style),
                    Paragraph(str(kpis.get('resolved_count', 0)), kpi_val_style),
                    Paragraph(str(kpis.get('pending_count', 0)), kpi_val_style),
                    Paragraph(str(kpis.get('overdue_count', 0)), kpi_val_style),
                    Paragraph(f"{kpis.get('resolution_rate', 0)}%", kpi_val_style),
                    Paragraph(f"₹{kpis.get('total_gr_amount', 0):,.2f}", kpi_val_style),
                ]
            ]
            kpi_table = Table(kpi_data, colWidths=[1.6 * inch] * 6)
            kpi_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
                ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(kpi_table)
            story.append(Spacer(1, 10))

        # 4. Records Table
        col_keys = [c['id'] for c in columns]
        col_labels = [c['label'] for c in columns]
        num_cols = len(col_keys)

        # Distribute available landscape width (10.2 inches) across columns
        total_width = 10.2 * inch
        col_width = total_width / max(1, num_cols)

        table_data = []

        # Header Row
        header_cells = [Paragraph(f"<b>{label}</b>", th_style) for label in col_labels]
        table_data.append(header_cells)

        # Data Rows
        for r_idx, rec in enumerate(records):
            row_cells = []
            for col_id in col_keys:
                raw_val = rec.get(col_id, '')
                if col_id == 'GR Amount' and raw_val:
                    cell_text = f"₹{cls.parse_float(raw_val):,.2f}"
                else:
                    cell_text = str(raw_val or '-')
                    # Shorten extra long descriptions for compact PDF rendering
                    if len(cell_text) > 80:
                        cell_text = cell_text[:77] + '...'
                row_cells.append(Paragraph(cell_text, td_style))
            table_data.append(row_cells)

        rec_table = Table(table_data, colWidths=[col_width] * num_cols, repeatRows=1)

        t_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]

        # Alternating row background
        for row_idx in range(1, len(table_data)):
            if row_idx % 2 == 0:
                t_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor('#f8fafc')))

        rec_table.setStyle(TableStyle(t_style))
        story.append(rec_table)

        doc.build(story, canvasmaker=NumberedCanvas)
        buf.seek(0)
        return buf.getvalue()
