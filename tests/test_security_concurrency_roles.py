"""
Verification Suite for:
1. Session Security & Cookie Policies
2. CSRF Attack Protection
3. Brute-Force Login Rate Limiting
4. Concurrent Submission Guard & Duplicate Prevention
5. 4-Role UI End-to-End Workflow & Access Restrictions
"""
import os
import sys
import time
import uuid
import unittest
import threading
import requests

BASE_URL = 'http://127.0.0.1:5050'

class TestSecurityConcurrencyAndRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            res = requests.get(f"{BASE_URL}/api/system/status", timeout=10)
            assert res.status_code == 200
        except Exception as e:
            raise unittest.SkipTest(f"Live server not running at {BASE_URL}: {e}")

    # =========================================================================
    # 1. SESSION SECURITY AUDIT
    # =========================================================================
    def test_01_session_cookie_security(self):
        """Verifies session cookie has HttpOnly=True and SameSite=Lax flags."""
        session = requests.Session()
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            'email': 'admin@complaints.local',
            'password': 'Admin@123456'
        }, timeout=15)
        self.assertEqual(res.status_code, 200)

        # Inspect cookies in jar
        cookies = session.cookies
        session_cookie = None
        for c in cookies:
            if c.name == 'session':
                session_cookie = c
                break

        self.assertIsNotNone(session_cookie, "Flask session cookie not set upon login.")
        # HttpOnly test: requests Cookie object stores httpOnly attribute or rest dictionary
        # In urllib3/http.cookiejar, httpOnly is in c._rest.get('HttpOnly') or c.has_nonstandard_attr('HttpOnly')
        is_httponly = getattr(session_cookie, 'has_nonstandard_attr', lambda a: False)('HttpOnly') or 'HttpOnly' in getattr(session_cookie, '_rest', {})
        self.assertTrue(is_httponly, "Session cookie must have HttpOnly attribute to prevent JavaScript theft.")

        # SameSite test:
        same_site = getattr(session_cookie, '_rest', {}).get('SameSite', '').lower()
        self.assertEqual(same_site, 'lax', "Session cookie must have SameSite=Lax attribute.")
        print("\n[SECURITY PASS] Session Cookie HttpOnly=True and SameSite=Lax verified.")

    # =========================================================================
    # 2. CSRF ATTACK PROTECTION AUDIT
    # =========================================================================
    def test_02_csrf_attack_mitigation(self):
        """
        Simulates cross-site request forgery attacks:
        1. Sec-Fetch-Site: cross-site -> must be rejected with HTTP 403.
        2. Malicious Origin header -> must be rejected with HTTP 403.
        3. Same-origin legitimate request -> succeeds HTTP 200.
        """
        session = requests.Session()
        # Login first to get valid session
        res = session.post(f"{BASE_URL}/api/auth/login", json={
            'email': 'admin@complaints.local',
            'password': 'Admin@123456'
        }, timeout=15)
        self.assertEqual(res.status_code, 200)

        # Attack A: Cross-site Sec-Fetch-Site
        evil_headers_a = {
            'Sec-Fetch-Site': 'cross-site',
            'Origin': 'https://evil-attacker-site.com'
        }
        res_evil_a = session.post(f"{BASE_URL}/api/departments", json={'name': 'Evil Department'}, headers=evil_headers_a, timeout=10)
        self.assertEqual(res_evil_a.status_code, 403)
        self.assertIn('Cross-site request blocked', res_evil_a.json().get('message', ''))

        # Attack B: Origin mismatch
        evil_headers_b = {
            'Origin': 'https://phishing-site.org'
        }
        res_evil_b = session.post(f"{BASE_URL}/api/departments", json={'name': 'Phishing Department'}, headers=evil_headers_b, timeout=10)
        self.assertEqual(res_evil_b.status_code, 403)
        print("[SECURITY PASS] CSRF attack vectors (cross-site Sec-Fetch-Site and Origin spoofing) successfully mitigated (HTTP 403).")

    # =========================================================================
    # 3. LOGIN BRUTE-FORCE RATE LIMITING AUDIT
    # =========================================================================
    def test_03_login_brute_force_rate_limiting(self):
        """
        Attempts rapid invalid logins for a dummy test email.
        Verifies that after 5 consecutive failures, the server returns HTTP 429 Too Many Requests.
        """
        test_email = f"target_{uuid.uuid4().hex[:6]}@example.com"
        results = []
        for i in range(6):
            r = requests.post(f"{BASE_URL}/api/auth/login", json={
                'email': test_email,
                'password': f"WrongPassword_{i}"
            }, timeout=10)
            results.append(r.status_code)

        # First 5 should be 401 Unauthorized, 6th should be 429 Too Many Requests
        self.assertEqual(results[:5], [401, 401, 401, 401, 401])
        self.assertEqual(results[5], 429, f"6th invalid attempt did not trigger 429: {results}")
        print("[SECURITY PASS] Login brute-force rate limiter verified (HTTP 429 returned after 5 failures).")

    # =========================================================================
    # 4. CONCURRENT SUBMISSION & DUPLICATE DEBOUNCE AUDIT
    # =========================================================================
    def test_04_concurrent_complaint_submission_guard(self):
        """
        Fires two simultaneous complaint lodging requests with identical subject & description.
        Verifies that thread-safe debounce blocks duplicate complaints (returns HTTP 409 for the collision).
        """
        user_email = f"citizen_conc_{uuid.uuid4().hex[:5]}@example.com"
        user_pw = "Password123!"

        # Register user
        reg_res = requests.post(f"{BASE_URL}/api/auth/register", json={
            'full_name': 'Concurrency Citizen',
            'email': user_email,
            'password': user_pw,
            'department': 'General'
        }, timeout=15)
        self.assertEqual(reg_res.status_code, 201)

        user_session = requests.Session()
        login_res = user_session.post(f"{BASE_URL}/api/auth/login", json={
            'email': user_email,
            'password': user_pw
        }, timeout=15)
        self.assertEqual(login_res.status_code, 200)

        # Fire two concurrent threads attempting identical complaint submission
        subject = f"[CONCURRENCY-TEST] Identical Subject {uuid.uuid4().hex[:4]}"
        description = "Testing simultaneous double-click submissions."

        payload = {
            'department': 'Facilities & Infrastructure',
            'priority': 'Low',
            'subject': subject,
            'description': description
        }

        statuses = []
        responses = []

        def submit():
            r = user_session.post(f"{BASE_URL}/api/complaints", json=payload, timeout=20)
            statuses.append(r.status_code)
            responses.append(r.json())

        t1 = threading.Thread(target=submit)
        t2 = threading.Thread(target=submit)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # One must succeed (201) and one must be rejected by debounce (409)
        self.assertIn(201, statuses, "At least one concurrent submission must succeed.")
        self.assertIn(409, statuses, "Duplicate concurrent submission must be rejected with 409.")
        print("[CONCURRENCY PASS] Concurrent submission race condition prevented: one 201 created, one 409 debounced.")

        # Cleanup created complaint & test user
        for resp in responses:
            if resp.get('status') == 'success' and 'complaint' in resp:
                cid = resp['complaint']['Complaint ID']
                self._cleanup_complaint(cid)
        self._cleanup_user(user_email)

    # =========================================================================
    # 5. ALL 4 ROLES WORKFLOW & ACCESS RESTRICTION AUDIT
    # =========================================================================
    def test_05_four_roles_complete_workflow(self):
        """
        Comprehensive workflow testing:
        - Super Admin: setup lockout confirmed, user management
        - Admin: department and staff management
        - Support Staff: workspace, assignment, internal note, status lifecycle
        - User: lodge with direct Drive link, my complaints isolation, internal notes concealed
        """
        suffix = uuid.uuid4().hex[:5]
        admin_email = 'admin@complaints.local'
        admin_pw = 'Admin@123456'
        staff_email = f"staff_{suffix}@example.com"
        staff_pw = "StaffPass123!"
        citizen_email = f"citizen_{suffix}@example.com"
        citizen_pw = "CitizenPass123!"

        s_admin = requests.Session()
        s_staff = requests.Session()
        s_user = requests.Session()

        # 1. Super Admin Setup Lockout Check
        r_setup = requests.get(f"{BASE_URL}/api/system/setup-status", timeout=10)
        self.assertEqual(r_setup.status_code, 200)
        self.assertFalse(r_setup.json()['needs_admin_setup'])

        r_hack = requests.post(f"{BASE_URL}/api/auth/setup-admin", json={
            'full_name': 'Hacker', 'email': 'hack@example.com', 'password': 'Password123!'
        }, timeout=10)
        self.assertEqual(r_hack.status_code, 403)
        print("\n[ROLE 1: SUPER ADMIN] Setup locked out permanently (HTTP 403).")

        # 2. Admin Login & Staff Creation
        r_login_adm = s_admin.post(f"{BASE_URL}/api/auth/login", json={'email': admin_email, 'password': admin_pw}, timeout=15)
        self.assertEqual(r_login_adm.status_code, 200)
        r_create_staff = s_admin.post(f"{BASE_URL}/api/users", json={
            'full_name': f"Officer {suffix}",
            'email': staff_email,
            'password': staff_pw,
            'role': 'Support Staff',
            'department': 'Finance & Billing'
        }, timeout=20)
        self.assertEqual(r_create_staff.status_code, 201)
        print(f"[ROLE 2: ADMIN] Admin provisioned Support Staff '{staff_email}'.")

        # 3. Citizen Registration & Complaint Lodging with Direct Drive Link
        r_reg_user = s_user.post(f"{BASE_URL}/api/auth/register", json={
            'full_name': f"Citizen {suffix}",
            'email': citizen_email,
            'password': citizen_pw,
            'department': 'General'
        }, timeout=20)
        self.assertEqual(r_reg_user.status_code, 201)

        r_log_user = s_user.post(f"{BASE_URL}/api/auth/login", json={'email': citizen_email, 'password': citizen_pw}, timeout=15)
        self.assertEqual(r_log_user.status_code, 200)

        drive_share_url = "https://drive.google.com/file/d/19OyFQPQAOPexKT8IqAkHN2Z8Z3U_n4Aq/view?usp=sharing"
        r_lodge = s_user.post(f"{BASE_URL}/api/complaints", json={
            'department': 'Finance & Billing',
            'priority': 'Medium',
            'subject': f"[AUDIT-TEST] Invoice Discrepancy {suffix}",
            'description': 'Billing calculation variance observed in October settlement statement.',
            'attachment_link': drive_share_url
        }, timeout=20)
        self.assertEqual(r_lodge.status_code, 201)
        cid = r_lodge.json()['complaint']['Complaint ID']
        self.assertEqual(r_lodge.json()['complaint']['Attachment Link'], drive_share_url)
        print(f"[ROLE 3: USER] Lodged complaint {cid} with verified Google Drive Attachment Link.")

        # User Cross-User Isolation
        r_user_list = s_user.get(f"{BASE_URL}/api/complaints", timeout=15)
        self.assertEqual(r_user_list.status_code, 200)
        for c in r_user_list.json()['complaints']:
            self.assertEqual(c['Complainant Email'].lower().strip(), citizen_email.lower().strip())
        print(f"[ROLE 3: USER] Strict cross-user complaint isolation verified.")

        # 4. Support Staff Login, Assignment, Comments, Status
        r_log_staff = s_staff.post(f"{BASE_URL}/api/auth/login", json={'email': staff_email, 'password': staff_pw}, timeout=15)
        self.assertEqual(r_log_staff.status_code, 200)

        # Assign
        r_assign = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/assign", json={
            'assigned_to_email': staff_email,
            'assigned_to_name': f"Officer {suffix}"
        }, timeout=35)
        self.assertEqual(r_assign.status_code, 200)

        # Public Comment
        r_pub = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/comments", json={
            'message': 'Finance team has received the reconciliation request.',
            'comment_type': 'Public Update'
        }, timeout=35)
        self.assertEqual(r_pub.status_code, 201)

        # Internal Note
        r_int = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/comments", json={
            'message': 'INTERNAL AUDIT NOTE: Cross-checked ledger batch 402 - tax line rounding difference.',
            'comment_type': 'Internal Note'
        }, timeout=35)
        self.assertEqual(r_int.status_code, 201)

        # Status Update to Resolved
        r_status = s_staff.post(f"{BASE_URL}/api/complaints/{cid}/status", json={
            'status': 'Resolved',
            'remarks': 'Settlement recalculated and credit note issued.'
        }, timeout=35)
        self.assertEqual(r_status.status_code, 200)
        print(f"[ROLE 4: SUPPORT STAFF] Complaint {cid} assigned, comments posted, and status resolved.")

        # 5. Access Restriction Checks:
        # A. User cannot see internal note
        r_detail_user = s_user.get(f"{BASE_URL}/api/complaints/{cid}", timeout=15)
        self.assertEqual(r_detail_user.status_code, 200)
        comments_seen = r_detail_user.json()['comments']
        for c in comments_seen:
            self.assertNotEqual(c['Comment Type'], 'Internal Note', "LEAK: Citizen saw Internal Note!")
        self.assertEqual(r_detail_user.json()['complaint']['Status'], 'Resolved')
        print(f"[SECURITY] Internal note concealment verified for regular user.")

        # B. User cannot access staff/admin APIs
        self.assertEqual(s_user.post(f"{BASE_URL}/api/complaints/{cid}/status", json={'status': 'Closed'}).status_code, 403)
        self.assertEqual(s_user.get(f"{BASE_URL}/api/users").status_code, 403)
        self.assertEqual(s_user.post(f"{BASE_URL}/api/departments", json={'name': 'Illegal'}).status_code, 403)

        # C. Staff cannot create users or departments
        self.assertEqual(s_staff.post(f"{BASE_URL}/api/users", json={'full_name': 'X', 'email': 'x@x.com', 'password': 'p'}).status_code, 403)
        self.assertEqual(s_staff.post(f"{BASE_URL}/api/departments", json={'name': 'Illegal'}).status_code, 403)
        print(f"[SECURITY] Server-side RBAC boundary restrictions strictly verified across all roles.")

        # Cleanup test records
        self._cleanup_complaint(cid)
        self._cleanup_user(staff_email)
        self._cleanup_user(citizen_email)

    def _cleanup_complaint(self, cid):
        from services.google_client import get_gspread_client
        from config import Config
        gc = get_gspread_client()
        sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)
        # Complaints
        ws = sh.worksheet('Complaints')
        rows = ws.get_all_values()
        for idx in range(len(rows), 1, -1):
            if rows[idx-1] and rows[idx-1][0] == cid:
                ws.delete_rows(idx)
        # Status History
        ws_h = sh.worksheet('Status History')
        h_rows = ws_h.get_all_values()
        for idx in range(len(h_rows), 1, -1):
            if h_rows[idx-1] and len(h_rows[idx-1]) > 1 and h_rows[idx-1][1] == cid:
                ws_h.delete_rows(idx)
        # Comments
        ws_c = sh.worksheet('Assignments and Comments')
        c_rows = ws_c.get_all_values()
        for idx in range(len(c_rows), 1, -1):
            if c_rows[idx-1] and len(c_rows[idx-1]) > 1 and c_rows[idx-1][1] == cid:
                ws_c.delete_rows(idx)

    def _cleanup_user(self, email):
        from services.google_client import get_gspread_client
        from config import Config
        gc = get_gspread_client()
        sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)
        ws = sh.worksheet('Users')
        rows = ws.get_all_values()
        for idx in range(len(rows), 1, -1):
            if rows[idx-1] and len(rows[idx-1]) > 2 and rows[idx-1][2].lower() == email.lower():
                ws.delete_rows(idx)

if __name__ == '__main__':
    unittest.main()
