"""
Final End-to-End Production Smoke Test Suite.
Verifies:
- Responsive SPA assets and DOM views
- 4 Roles: Super Admin, Admin, Support Staff, Citizen User
- Login/Logout, Registration, First-run lockout
- Complaint creation with direct Google Drive Link
- Staff assignment, status lifecycle, Public vs Internal comment separation
- Strict server-side RBAC and authorization errors (HTTP 403)
- CSRF defense and brute-force login rate limiting (HTTP 429)
- Cold restart persistence directly from Google Sheets
- Clean-up of test rows preserving production records
"""
import os
import sys
import time
import uuid
import unittest
import requests

BASE_URL = 'http://127.0.0.1:5050'

class TestFinalBrowserSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            r = requests.get(f"{BASE_URL}/api/system/status", timeout=10)
            assert r.status_code == 200
        except Exception as e:
            raise unittest.SkipTest(f"Live server not online at {BASE_URL}: {e}")

    # =========================================================================
    # TEST 1: FRONTEND ASSETS, SPA DOM & RESPONSIVE LAYOUT VERIFICATION
    # =========================================================================
    def test_01_frontend_spa_and_responsive_markup(self):
        """Verifies SPA entry point, HTML templates, CSS responsive rules, and JS bundle."""
        # 1. HTML index
        r_index = requests.get(f"{BASE_URL}/", timeout=10)
        self.assertEqual(r_index.status_code, 200)
        html = r_index.text

        # Verify core SPA elements
        required_elements = [
            'id="auth-container"',
            'id="app-shell"',
            'id="login-form"',
            'id="register-form"',
            'id="setup-admin-form"',
            'id="view-dashboard"',
            'id="view-lodge"',
            'id="view-my-complaints"',
            'id="view-manage"',
            'id="view-settings"',
            'id="complaint-detail-modal"',
            'id="comp-attachment-link"',
            'id="comp-attachment"'
        ]
        for el in required_elements:
            self.assertIn(el, html, f"Missing required SPA DOM element: {el}")

        # Verify binary upload blocked notice and Drive link recommendation in HTML
        self.assertIn('BLOCKED without OAuth/Shared Drive', html)
        self.assertIn('Google Drive / Cloud Share Link (Recommended)', html)

        # 2. CSS Stylesheet & Responsive Breakpoints
        r_css = requests.get(f"{BASE_URL}/static/css/style.css", timeout=10)
        self.assertEqual(r_css.status_code, 200)
        css = r_css.text
        self.assertIn('@media (max-width: 900px)', css, "Missing responsive mobile/tablet breakpoint in CSS.")

        # 3. JavaScript SPA Bundle
        r_js = requests.get(f"{BASE_URL}/static/js/app.js", timeout=10)
        self.assertEqual(r_js.status_code, 200)
        js = r_js.text
        self.assertIn('function switchView', js)
        self.assertIn('function handleLodgeComplaint', js)
        self.assertIn('function openComplaintModal', js)

        print("\n[SMOKE 1: FRONTEND PASS] SPA index HTML, responsive CSS (@media 900px), and JS bundle verified.")

    # =========================================================================
    # TEST 2: SECURITY DEFENSES (CSRF & LOGIN RATE LIMITING)
    # =========================================================================
    def test_02_security_csrf_and_rate_limiting(self):
        """Verifies CSRF blocking and brute-force login rate limiting."""
        # CSRF test: cross-site origin on state-changing authenticated POST
        s_admin = requests.Session()
        r_log = s_admin.post(f"{BASE_URL}/api/auth/login", json={'email': 'admin@complaints.local', 'password': 'Admin@123456'}, timeout=15)
        self.assertEqual(r_log.status_code, 200)

        # Cross-site attack simulation
        r_csrf = s_admin.post(
            f"{BASE_URL}/api/departments",
            json={'name': 'CSRF Attack Department'},
            headers={'Sec-Fetch-Site': 'cross-site'},
            timeout=10
        )
        self.assertEqual(r_csrf.status_code, 403, "CSRF cross-site request was not blocked.")

        # Rate limiting test: 5 bad passwords then 429
        dummy_email = f"rate_probe_{uuid.uuid4().hex[:6]}@example.com"
        for i in range(5):
            r = requests.post(f"{BASE_URL}/api/auth/login", json={'email': dummy_email, 'password': 'WrongPassword!'}, timeout=10)
            self.assertEqual(r.status_code, 401)
        r_limit = requests.post(f"{BASE_URL}/api/auth/login", json={'email': dummy_email, 'password': 'WrongPassword!'}, timeout=10)
        self.assertEqual(r_limit.status_code, 429, "Excessive failed logins did not trigger HTTP 429.")
        print("[SMOKE 2: SECURITY PASS] CSRF attack blocked (HTTP 403) & login rate limiting enforced (HTTP 429).")

    # =========================================================================
    # TEST 3: FULL 4-ROLE WORKFLOW & PERSISTENCE
    # =========================================================================
    def test_03_four_roles_lifecycle_and_sheets_persistence(self):
        """
        Executes end-to-end user lifecycle across all 4 roles:
        1. Super Admin: verify setup lockout (403), verify session login.
        2. Admin: create Support Staff user and custom Department.
        3. Citizen User: register, login, lodge complaint with Google Drive Link.
        4. Cross-user isolation: user only sees their own complaints.
        5. Support Staff: workspace, assignment, public comment, internal note, status change.
        6. Access restrictions: user cannot see internal note, cannot access admin routes (403).
        7. Cold restart persistence from Google Sheets.
        8. Clean-up test rows.
        """
        suffix = uuid.uuid4().hex[:5]
        admin_email = 'admin@complaints.local'
        admin_pw = 'Admin@123456'
        staff_email = f"final_staff_{suffix}@example.com"
        staff_pw = "StaffSecret123!"
        user_email = f"final_user_{suffix}@example.com"
        user_pw = "UserSecret123!"
        dept_name = f"Logistics & Transit {suffix}"

        s_admin = requests.Session()
        s_staff = requests.Session()
        s_user = requests.Session()

        # Step A: Super Admin lockout check
        r_status = requests.get(f"{BASE_URL}/api/system/setup-status", timeout=10)
        self.assertEqual(r_status.status_code, 200)
        self.assertFalse(r_status.json()['needs_admin_setup'])

        r_lockout = requests.post(f"{BASE_URL}/api/auth/setup-admin", json={
            'full_name': 'Hacker', 'email': 'hacker@example.com', 'password': 'Password123!'
        }, timeout=10)
        self.assertEqual(r_lockout.status_code, 403)
        print("\n[ROLE 1: SUPER ADMIN] First-run setup permanently locked (HTTP 403).")

        # Step B: Admin login & provisioning
        r_adm_log = s_admin.post(f"{BASE_URL}/api/auth/login", json={'email': admin_email, 'password': admin_pw}, timeout=15)
        self.assertEqual(r_adm_log.status_code, 200)

        # Admin creates department
        r_dept = s_admin.post(f"{BASE_URL}/api/departments", json={
            'name': dept_name,
            'description': 'Automated final smoke test logistics category',
            'lead_email': admin_email
        }, timeout=30)
        self.assertEqual(r_dept.status_code, 201)

        # Admin creates staff user
        r_staff_cr = s_admin.post(f"{BASE_URL}/api/users", json={
            'full_name': f"Officer {suffix}",
            'email': staff_email,
            'password': staff_pw,
            'role': 'Support Staff',
            'department': dept_name
        }, timeout=30)
        self.assertEqual(r_staff_cr.status_code, 201)
        print(f"[ROLE 2: ADMIN] Created Department '{dept_name}' and Support Staff '{staff_email}'.")

        # Step C: Citizen user registration & login
        r_reg = s_user.post(f"{BASE_URL}/api/auth/register", json={
            'full_name': f"Citizen {suffix}",
            'email': user_email,
            'password': user_pw,
            'department': 'General'
        }, timeout=30)
        self.assertEqual(r_reg.status_code, 201)

        r_user_log = s_user.post(f"{BASE_URL}/api/auth/login", json={'email': user_email, 'password': user_pw}, timeout=15)
        self.assertEqual(r_user_log.status_code, 200)
        self.assertEqual(r_user_log.json()['user']['role'], 'User')

        # Step D: User lodges complaint with Direct Drive Link
        drive_link = "https://drive.google.com/file/d/19OyFQPQAOPexKT8IqAkHN2Z8Z3U_n4Aq/view?usp=sharing"
        r_lodge = s_user.post(f"{BASE_URL}/api/complaints", json={
            'department': dept_name,
            'priority': 'High',
            'subject': f"[FINAL-SMOKE] Transit Schedule Disruption {suffix}",
            'description': 'Morning shuttle route delayed by 45 minutes.',
            'attachment_link': drive_link
        }, timeout=30)
        self.assertEqual(r_lodge.status_code, 201)
        cid = r_lodge.json()['complaint']['Complaint ID']
        self.assertEqual(r_lodge.json()['complaint']['Attachment Link'], drive_link)
        print(f"[ROLE 3: USER] Lodged complaint {cid} with verified Google Drive Attachment Link.")

        # Verify cross-user isolation
        r_my = s_user.get(f"{BASE_URL}/api/complaints", timeout=15)
        self.assertEqual(r_my.status_code, 200)
        for comp in r_my.json()['complaints']:
            self.assertEqual(comp['Complainant Email'].lower().strip(), user_email.lower().strip())
        print(f"[ROLE 3: USER] Cross-user isolation verified.")

        # Step E: Support Staff login, assignment, comments, status
        r_staff_log = s_staff.post(f"{BASE_URL}/api/auth/login", json={'email': staff_email, 'password': staff_pw}, timeout=15)
        self.assertEqual(r_staff_log.status_code, 200)
        self.assertEqual(r_staff_log.json()['user']['role'], 'Support Staff')

        # Assign
        r_assign = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/assign", json={
            'assigned_to_email': staff_email,
            'assigned_to_name': f"Officer {suffix}"
        }, timeout=35)
        self.assertEqual(r_assign.status_code, 200)

        # Public Comment
        r_pub = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/comments", json={
            'message': 'Transit dispatch supervisor has re-routed backup vehicle.',
            'comment_type': 'Public Update'
        }, timeout=35)
        self.assertEqual(r_pub.status_code, 201)

        # Internal Note
        r_int = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/comments", json={
            'message': 'INTERNAL OPS NOTE: Shuttle #4 radiator hose replaced.',
            'comment_type': 'Internal Note'
        }, timeout=35)
        self.assertEqual(r_int.status_code, 201)

        # Status Update -> Resolved
        r_stat = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/status", json={
            'status': 'Resolved',
            'remarks': 'Replacement vehicle restored regular schedule.'
        }, timeout=35)
        self.assertEqual(r_stat.status_code, 200)
        print(f"[ROLE 4: SUPPORT STAFF] Complaint {cid} assigned, comments recorded, and resolved.")

        # Step F: Privacy check: User cannot see internal note
        r_view_u = s_user.get(f"{BASE_URL}/api/complaints/{cid}", timeout=15)
        self.assertEqual(r_view_u.status_code, 200)
        c_types = [c.get('Comment Type') for c in r_view_u.json()['comments']]
        self.assertIn('Public Update', c_types)
        self.assertNotIn('Internal Note', c_types, "LEAK: Regular user saw Internal Note!")
        print("[SECURITY PASS] Regular user sees Public Update; Internal Note concealed.")

        # Step G: RBAC Boundary enforcement (Authorization Errors)
        # User trying admin routes
        self.assertEqual(s_user.post(f"{BASE_URL}/api/departments", json={'name': 'Forbidden'}).status_code, 403)
        self.assertEqual(s_user.get(f"{BASE_URL}/api/users").status_code, 403)
        self.assertEqual(s_user.post(f"{BASE_URL}/api/complaints/{cid}/status", json={'status': 'Closed'}).status_code, 403)
        # Staff trying admin routes
        self.assertEqual(s_staff.post(f"{BASE_URL}/api/users", json={'full_name': 'x', 'email': 'x@x.com', 'password': 'p'}).status_code, 403)
        self.assertEqual(s_staff.post(f"{BASE_URL}/api/departments", json={'name': 'Forbidden'}).status_code, 403)
        print("[SECURITY PASS] Server-side RBAC boundary errors verified (all unauthorized calls return HTTP 403).")

        # Step H: Direct Google Sheets Cold Persistence Check
        from services.sheets_db import SheetsDB
        from services.google_client import get_gspread_client
        from config import Config
        # Invalidate all in-memory caches to force reading fresh data from Google Sheets
        SheetsDB._invalidate_cache()
        gc = get_gspread_client()
        sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)

        # Check complaint in Complaints sheet
        ws_comp = sh.worksheet('Complaints')
        all_comp_values = ws_comp.get_all_values()
        found_sheet_comp = None
        for row in all_comp_values:
            if row and row[0] == cid:
                found_sheet_comp = row
                break
        self.assertIsNotNone(found_sheet_comp, f"Complaint {cid} not found in Google Sheets Complaints sheet!")
        self.assertEqual(found_sheet_comp[8], 'Resolved', f"Status not Resolved in Google Sheet: {found_sheet_comp[8]}")
        self.assertEqual(found_sheet_comp[11], drive_link, "Attachment Link not persisted in Google Sheet!")
        print(f"[PERSISTENCE PASS] Complaint {cid} verified persistent in live Google Sheets row with status 'Resolved'.")

        # Step I: Clean up only test records
        for idx in range(len(all_comp_values), 1, -1):
            if all_comp_values[idx-1] and all_comp_values[idx-1][0] == cid:
                ws_comp.delete_rows(idx)

        ws_h = sh.worksheet('Status History')
        h_vals = ws_h.get_all_values()
        for idx in range(len(h_vals), 1, -1):
            if h_vals[idx-1] and len(h_vals[idx-1]) > 1 and h_vals[idx-1][1] == cid:
                ws_h.delete_rows(idx)

        ws_c = sh.worksheet('Assignments and Comments')
        c_vals = ws_c.get_all_values()
        for idx in range(len(c_vals), 1, -1):
            if c_vals[idx-1] and len(c_vals[idx-1]) > 1 and c_vals[idx-1][1] == cid:
                ws_c.delete_rows(idx)

        ws_u = sh.worksheet('Users')
        u_vals = ws_u.get_all_values()
        for idx in range(len(u_vals), 1, -1):
            if u_vals[idx-1] and len(u_vals[idx-1]) > 2 and u_vals[idx-1][2] in [staff_email, user_email]:
                ws_u.delete_rows(idx)

        ws_d = sh.worksheet('Departments')
        d_vals = ws_d.get_all_values()
        for idx in range(len(d_vals), 1, -1):
            if d_vals[idx-1] and len(d_vals[idx-1]) > 1 and d_vals[idx-1][1] == dept_name:
                ws_d.delete_rows(idx)

        SheetsDB._invalidate_cache()
        print("[CLEANUP PASS] Test resources purged from Google Sheets. Production records left intact.")

if __name__ == '__main__':
    unittest.main()
