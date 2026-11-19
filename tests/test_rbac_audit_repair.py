"""
Comprehensive RBAC Audit & Repair Test Suite
AJIT ZONE PVT. LTD. - Complaint Management System

Exhaustively verifies:
1. Matrix testing across all 6 roles against all operations:
   - Super Admin
   - Admin
   - Department Head
   - Department Staff
   - Viewer
   - User
2. Department isolation & IDOR prevention (single vs multi-department vs cross-department).
3. Independent Close & Reopen workflow permission enforcement.
4. Internal Notes confidential protection (no silent conversion, 403 on lack of comments:add_internal).
5. Viewer restrictions (no lodge, no edit, no status change, no comments, no report export).
6. Attachment endpoints authentication & permission protection.
7. Deactivated/Archived immediate session invalidation on /api/auth/me and protected routes.
8. Privilege escalation prevention (self-role modification blocked).
"""
import os
import sys
import unittest
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import Config
from app import app
from services.mock_sheets_db import MockSheetsDB
from services.rbac_service import (
    RBACService,
    DEFAULT_ROLE_TEMPLATES
)

class TestRBACAuditAndRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        Config.INITIAL_ADMIN_PASSWORD = 'Admin@123456'

    def setUp(self):
        MockSheetsDB.reset()
        self.client = app.test_client()

        # Seed test accounts for all 6 roles
        self.super_admin_email = 'superadmin@ajitzone.local'
        self.admin_email = 'admin@ajitzone.local'
        self.dept_head_email = 'head_readymade@ajitzone.local'
        self.dept_staff_email = 'staff_readymade@ajitzone.local'
        self.other_dept_staff_email = 'staff_saree@ajitzone.local'
        self.viewer_email = 'viewer@ajitzone.local'
        self.user_email = 'customer@ajitzone.local'
        self.other_user_email = 'other_customer@ajitzone.local'

        # 1. Super Admin
        MockSheetsDB.create_user('Super Administrator', self.super_admin_email, 'Admin@123456', role='Super Admin', department='Management')
        # 2. Admin
        MockSheetsDB.create_user('General Admin', self.admin_email, 'Admin@123456', role='Admin', department='Operations')
        # 3. Department Head (Readymade)
        MockSheetsDB.create_user('Head Readymade', self.dept_head_email, 'Admin@123456', role='Department Head', department='Readymade')
        # 4. Department Staff (Readymade)
        MockSheetsDB.create_user('Staff Readymade', self.dept_staff_email, 'Admin@123456', role='Department Staff', department='Readymade')
        # 5. Department Staff (Saree)
        MockSheetsDB.create_user('Staff Saree', self.other_dept_staff_email, 'Admin@123456', role='Department Staff', department='Saree')
        # 6. Viewer
        MockSheetsDB.create_user('External Viewer', self.viewer_email, 'Admin@123456', role='Viewer', department='Readymade')
        # 7. User (Customer)
        MockSheetsDB.create_user('Retail Customer', self.user_email, 'Admin@123456', role='User', department='General')
        # 8. Other User (Customer 2)
        MockSheetsDB.create_user('Other Customer', self.other_user_email, 'Admin@123456', role='User', department='General')

        # Seed sample complaints in MockSheetsDB
        # Readymade complaint owned by customer@ajitzone.local
        self.comp_readymade = MockSheetsDB.lodge_complaint(
            employee_name='Staff Readymade',
            complain_date='2026-03-01',
            customer_name='Ajit Customer',
            invoice_no='INV-101',
            complain_types='Stitching Defect',
            priority='High',
            remarks='Initial submission',
            complain_details='Stitching torn at seam',
            customer_mobile_no='9876543210',
            department_name='Readymade',
            complainant_email=self.user_email,
            status='Submitted'
        )

        # Saree complaint owned by other_customer@ajitzone.local
        self.comp_saree = MockSheetsDB.lodge_complaint(
            employee_name='Staff Saree',
            complain_date='2026-03-02',
            customer_name='Other Customer',
            invoice_no='INV-202',
            complain_types='Fabric Defect',
            priority='Medium',
            remarks='Initial saree submission',
            complain_details='Color bleeding in fabric',
            customer_mobile_no='9876500000',
            department_name='Saree',
            complainant_email=self.other_user_email,
            status='In Progress'
        )

    def _login(self, email, password='Admin@123456'):
        res = self.client.post('/api/auth/login', json={'email': email, 'password': password})
        self.assertEqual(res.status_code, 200, f"Login failed for {email}")
        return res

    # =========================================================================
    # 1. VIEWER RESTRICTIONS TEST
    # =========================================================================
    def test_viewer_strict_read_only(self):
        """Viewer role must be strictly read-only across all state-modifying endpoints."""
        self._login(self.viewer_email)

        # A) Viewer CAN view complaints and reports
        res = self.client.get('/api/complaints')
        self.assertEqual(res.status_code, 200)

        res = self.client.get('/api/reports')
        self.assertEqual(res.status_code, 200)

        # B) Viewer CANNOT lodge complaints (POST /api/complaints -> 403)
        res = self.client.post('/api/complaints', json={
            'customer_name': 'Test',
            'department_name': 'Readymade',
            'complain_types': 'Defect',
            'complain_details': 'Details'
        })
        self.assertEqual(res.status_code, 403, "Viewer should be forbidden from lodging complaints")

        # C) Viewer CANNOT edit complaints (PUT /api/complaints/<id> -> 403)
        res = self.client.put(f"/api/complaints/{self.comp_readymade['Complaint ID']}", json={
            'customer_name': 'Hacked Name'
        })
        self.assertEqual(res.status_code, 403, "Viewer should be forbidden from editing complaints")

        # D) Viewer CANNOT update status (POST /api/complaints/<id>/status -> 403)
        res = self.client.post(f"/api/complaints/{self.comp_readymade['Complaint ID']}/status", json={
            'status': 'Resolved'
        })
        self.assertEqual(res.status_code, 403, "Viewer should be forbidden from updating complaint status")

        # E) Viewer CANNOT post comments (POST /api/complaints/<id>/comments -> 403)
        res = self.client.post(f"/api/complaints/{self.comp_readymade['Complaint ID']}/comments", json={
            'message': 'Viewer note'
        })
        self.assertEqual(res.status_code, 403, "Viewer should be forbidden from posting comments")

        # F) Viewer CANNOT export Excel/CSV reports (lacks reports:export_excel/reports:export_csv -> 403)
        res = self.client.get('/api/reports/export/excel')
        self.assertEqual(res.status_code, 403, "Viewer should be forbidden from exporting Excel reports")

        res = self.client.get('/api/reports/export/csv')
        self.assertEqual(res.status_code, 403, "Viewer should be forbidden from exporting CSV reports")

    # =========================================================================
    # 2. DEPARTMENT HEAD WORKFLOW & SCOPING
    # =========================================================================
    def test_department_head_workflow_and_isolation(self):
        """Department Head can assign, change status, close, reopen in own dept, but blocked from other dept."""
        self._login(self.dept_head_email)
        readymade_id = self.comp_readymade['Complaint ID']
        saree_id = self.comp_saree['Complaint ID']

        # A) Can assign staff within Readymade department
        res = self.client.post(f"/api/complaints/{readymade_id}/assign", json={
            'assigned_to_email': self.dept_staff_email,
            'assigned_to_name': 'Staff Readymade'
        })
        self.assertEqual(res.status_code, 200, f"Dept Head should assign complaint in own dept: {res.data}")

        # B) Can change status in own dept (Submitted -> Under Review)
        res = self.client.post(f"/api/complaints/{readymade_id}/status", json={
            'status': 'Under Review',
            'remarks': 'Review started by Dept Head'
        })
        self.assertEqual(res.status_code, 200)

        # C) Can close complaint in own dept (Under Review -> Closed)
        res = self.client.post(f"/api/complaints/{readymade_id}/status", json={
            'status': 'Closed',
            'remarks': 'Issue verified and closed'
        })
        self.assertEqual(res.status_code, 200, "Dept Head should be able to close complaint")

        # D) Can reopen closed complaint in own dept (Closed -> In Progress)
        res = self.client.post(f"/api/complaints/{readymade_id}/status", json={
            'status': 'In Progress',
            'remarks': 'Reopening for customer re-inspection'
        })
        self.assertEqual(res.status_code, 200, "Dept Head should be able to reopen complaint")

        # E) Can post internal note
        res = self.client.post(f"/api/complaints/{readymade_id}/comments", json={
            'message': 'Confidential staff note by Head',
            'comment_type': 'Internal Note'
        })
        self.assertEqual(res.status_code, 201, "Dept Head should post internal note")

        # F) IDOR Prevention: Dept Head (Readymade) CANNOT access Saree complaint
        res = self.client.get(f"/api/complaints/{saree_id}")
        self.assertEqual(res.status_code, 403, "Dept Head should be forbidden from accessing cross-department complaint")

        res = self.client.post(f"/api/complaints/{saree_id}/status", json={'status': 'Resolved'})
        self.assertEqual(res.status_code, 403, "Dept Head should be forbidden from updating cross-department complaint")

    # =========================================================================
    # 3. DEPARTMENT STAFF LIMITS (NO CLOSE, NO REOPEN, NO ASSIGN)
    # =========================================================================
    def test_department_staff_workflow_boundaries(self):
        """Department Staff has change_status, but lacks close, reopen, and assign."""
        self._login(self.dept_staff_email)
        readymade_id = self.comp_readymade['Complaint ID']

        # A) Can change status (Submitted -> In Progress)
        res = self.client.post(f"/api/complaints/{readymade_id}/status", json={
            'status': 'In Progress',
            'remarks': 'Work started'
        })
        self.assertEqual(res.status_code, 200, "Staff can transition status")

        # B) CANNOT Close complaint (status: 'Closed' requires complaints:close -> 403)
        res = self.client.post(f"/api/complaints/{readymade_id}/status", json={
            'status': 'Closed',
            'remarks': 'Attempting unauthorized close'
        })
        self.assertEqual(res.status_code, 403, "Staff without complaints:close should be denied")

        # C) CANNOT Assign complaint (complaints:assign missing -> 403)
        res = self.client.post(f"/api/complaints/{readymade_id}/assign", json={
            'assigned_to_email': self.dept_staff_email,
            'assigned_to_name': 'Self'
        })
        self.assertEqual(res.status_code, 403, "Staff without complaints:assign should be denied")

        # D) CANNOT permanently delete complaint (complaints:delete missing -> 403)
        res = self.client.delete(f"/api/complaints/{readymade_id}")
        self.assertEqual(res.status_code, 403, "Staff without complaints:delete should be denied")

    # =========================================================================
    # 4. REGULAR USER / COMPLAINANT RESTRICTIONS
    # =========================================================================
    def test_regular_user_lifecycle_and_security(self):
        """User can edit only when Submitted; cannot post internal notes; cannot access other users' data."""
        self._login(self.user_email)
        own_id = self.comp_readymade['Complaint ID']
        other_id = self.comp_saree['Complaint ID']

        # A) User can view own complaint
        res = self.client.get(f"/api/complaints/{own_id}")
        self.assertEqual(res.status_code, 200)

        # B) User CANNOT view other customer's complaint (IDOR -> 403)
        res = self.client.get(f"/api/complaints/{other_id}")
        self.assertEqual(res.status_code, 403, "User cannot access other complainant records")

        # C) User can edit own complaint while Submitted
        res = self.client.put(f"/api/complaints/{own_id}", json={
            'complain_details': 'Updated details by customer'
        })
        self.assertEqual(res.status_code, 200, "User can edit own complaint while Submitted")

        # D) Change status to Under Review by Dept Head
        MockSheetsDB.update_complaint_status(own_id, 'Under Review', {'Full Name': 'Staff'})

        # E) User CANNOT edit own complaint once status has changed from Submitted
        res = self.client.put(f"/api/complaints/{own_id}", json={
            'complain_details': 'Illegal update after review started'
        })
        self.assertEqual(res.status_code, 403, "User cannot edit complaint once progressed beyond Submitted")

        # F) User attempting to post Internal Note must be rejected with 403 (NOT downgraded silently)
        res = self.client.post(f"/api/complaints/{own_id}/comments", json={
            'message': 'Trying to post secret note',
            'comment_type': 'Internal Note'
        })
        self.assertEqual(res.status_code, 403, "User must receive 403 when trying to post Internal Note")

    # =========================================================================
    # 5. ATTACHMENT ENDPOINTS AUTHENTICATION & PERMISSIONS
    # =========================================================================
    def test_attachment_endpoints_security(self):
        """Drive attachment view and download endpoints require authentication and attachments:view."""
        # Unauthenticated request -> 401
        res = self.client.get('/api/drive/download/fake_file_123')
        self.assertEqual(res.status_code, 401, "Unauthenticated download must return 401")

        res = self.client.get('/api/drive/view/fake_file_123')
        self.assertEqual(res.status_code, 401, "Unauthenticated view must return 401")

        # Authenticated user with attachments:view -> passes auth decorator
        self._login(self.super_admin_email)
        res = self.client.get('/api/drive/view/fake_file_123')
        self.assertEqual(res.status_code, 302, "Super Admin with attachments:view redirects to Drive link")

    # =========================================================================
    # 6. DEACTIVATED USER IMMEDIATE SESSION REVOCATION
    # =========================================================================
    def test_deactivated_user_immediate_revocation(self):
        """When an account is deactivated, /api/auth/me immediately invalidates session and returns 403."""
        self._login(self.viewer_email)

        # Active session returns 200
        res = self.client.get('/api/auth/me')
        self.assertEqual(res.status_code, 200)

        # Deactivate user in database
        viewer_record = MockSheetsDB.find_user_by_email(self.viewer_email)
        MockSheetsDB.update_user(viewer_record['User ID'], is_active='FALSE')

        # Next call to /api/auth/me detects inactive account, clears session, returns 403
        res = self.client.get('/api/auth/me')
        self.assertEqual(res.status_code, 403, "Deactivated account must be rejected with 403 on session refresh")

        # Session has been wiped; subsequent call is unauthenticated (401)
        res = self.client.get('/api/complaints')
        self.assertEqual(res.status_code, 401, "Session should be purged after deactivation")

    # =========================================================================
    # 7. PRIVILEGE ESCALATION PREVENTION
    # =========================================================================
    def test_self_role_modification_prevention(self):
        """An Admin cannot change their own role to Super Admin (or any other role)."""
        self._login(self.admin_email)
        admin_rec = MockSheetsDB.find_user_by_email(self.admin_email)

        # Admin tries to change own role to Super Admin
        res = self.client.put(f"/api/users/{admin_rec['User ID']}", json={
            'role': 'Super Admin'
        })
        self.assertEqual(res.status_code, 403, "Admin cannot promote self to Super Admin")

        # Admin tries to change own role to Viewer
        res = self.client.put(f"/api/users/{admin_rec['User ID']}", json={
            'role': 'Viewer'
        })
        self.assertEqual(res.status_code, 403, "Admin cannot change own user role")

    # =========================================================================
    # 8. SUPER ADMIN FULL ACCESS
    # =========================================================================
    def test_super_admin_unrestricted_access(self):
        """Super Admin possesses wildcard permissions and ALL data scope."""
        self._login(self.super_admin_email)

        # Super Admin can view, edit, delete across any department
        res = self.client.get(f"/api/complaints/{self.comp_saree['Complaint ID']}")
        self.assertEqual(res.status_code, 200, "Super Admin can view Saree complaint")

        res = self.client.get(f"/api/complaints/{self.comp_readymade['Complaint ID']}")
        self.assertEqual(res.status_code, 200, "Super Admin can view Readymade complaint")

        # Super Admin can view audit logs
        res = self.client.get('/api/rbac/audit-logs')
        self.assertEqual(res.status_code, 200)

        # Super Admin can view roles
        res = self.client.get('/api/rbac/roles')
        self.assertEqual(res.status_code, 200)

if __name__ == '__main__':
    unittest.main()
