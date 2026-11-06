"""
Comprehensive verification of all 4 roles (User, Support Staff, Admin, Super Admin)
and Attachment Link persistence in Google Sheets.
"""
import os
import sys
import unittest
import requests
import uuid

BASE_URL = 'http://127.0.0.1:5050'

class TestRoleMatrixAndAttachment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify server is reachable
        try:
            res = requests.get(f"{BASE_URL}/api/system/status", timeout=10)
            assert res.status_code == 200
            cls.system_status = res.json()
        except Exception as e:
            raise unittest.SkipTest(f"Live server not running at {BASE_URL}: {e}")

    def test_01_super_admin_setup_lockout(self):
        """Confirms Super Admin first-run setup is securely locked out."""
        res = requests.get(f"{BASE_URL}/api/system/setup-status", timeout=10)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data.get('needs_admin_setup'))
        self.assertTrue(data.get('has_admin'))

        # Attempt to provision another Super Admin via the first-run endpoint -> must be 403 Forbidden
        res_setup = requests.post(f"{BASE_URL}/api/auth/setup-admin", json={
            'full_name': 'Hacker Admin',
            'email': 'hacker@example.com',
            'password': 'Password123!'
        }, timeout=10)
        self.assertEqual(res_setup.status_code, 403)
        print("\n[LIVE PASS] Super Admin setup lockout enforced (HTTP 403).")

    def test_02_all_four_roles_rbac_and_attachment_link(self):
        """
        Tests:
        1. Admin creates Support Staff user.
        2. Citizen registers User account.
        3. User lodges complaint with an attachment_link (Cloud Drive URL).
        4. User verifies complaint appears in their list.
        5. Support Staff views complaint, assigns it, posts Internal Note & Public Update.
        6. User views complaint: confirms Public Update is visible and Internal Note is hidden.
        7. User attempts Admin action (creating department) -> receives 403 Forbidden.
        8. Support Staff attempts Admin action (creating user) -> receives 403 Forbidden.
        9. Admin creates a new department successfully.
        10. Clean up test records from live Google Sheets.
        """
        suffix = uuid.uuid4().hex[:5]
        admin_email = 'admin@complaints.local'
        admin_pw = 'Admin@123456'
        
        staff_email = f"staff_{suffix}@example.com"
        staff_pw = "StaffSecret123!"
        
        user_email = f"user_{suffix}@example.com"
        user_pw = "UserSecret123!"

        session_admin = requests.Session()
        session_staff = requests.Session()
        session_user = requests.Session()

        # Step A: Admin Login
        res = session_admin.post(f"{BASE_URL}/api/auth/login", json={'email': admin_email, 'password': admin_pw}, timeout=15)
        self.assertEqual(res.status_code, 200, f"Admin login failed: {res.text}")
        admin_data = res.json()['user']
        self.assertIn(admin_data['role'], ['Admin', 'Super Admin'])
        print(f"[LIVE PASS] Super Admin / Admin logged in: {admin_data['name']} ({admin_data['role']})")

        # Step B: Admin creates Support Staff
        res = session_admin.post(f"{BASE_URL}/api/users", json={
            'full_name': f"Staff Officer {suffix}",
            'email': staff_email,
            'password': staff_pw,
            'role': 'Support Staff',
            'department': 'Operations & Facilities'
        }, timeout=20)
        self.assertEqual(res.status_code, 201, f"Failed to create support staff: {res.text}")
        print(f"[LIVE PASS] Admin created Support Staff user: {staff_email}")

        # Step C: Citizen registers User account
        res = session_user.post(f"{BASE_URL}/api/auth/register", json={
            'full_name': f"Citizen Tester {suffix}",
            'email': user_email,
            'password': user_pw,
            'department': 'General Public'
        }, timeout=20)
        self.assertEqual(res.status_code, 201, f"User registration failed: {res.text}")
        
        # User logs in
        res = session_user.post(f"{BASE_URL}/api/auth/login", json={'email': user_email, 'password': user_pw}, timeout=15)
        self.assertEqual(res.status_code, 200)
        user_data = res.json()['user']
        self.assertEqual(user_data['role'], 'User')
        print(f"[LIVE PASS] Citizen User registered and logged in: {user_email}")

        # Step D: User lodges complaint with direct attachment_link (Cloud Link)
        drive_link = f"https://drive.google.com/file/d/19OyFQPQAOPexKT8IqAkHN2Z8Z3U_n4Aq/view?usp=sharing"
        lodge_payload = {
            'department': 'Operations & Facilities',
            'priority': 'High',
            'subject': f"[TEST-LINK] Facility Access Problem {suffix}",
            'description': 'Elevator access card reader is intermittent at North entrance.',
            'attachment_link': drive_link
        }
        res = session_user.post(f"{BASE_URL}/api/complaints", json=lodge_payload, timeout=20)
        self.assertEqual(res.status_code, 201, f"Complaint lodging failed: {res.text}")
        created_complaint = res.json()['complaint']
        cid = created_complaint['Complaint ID']
        self.assertEqual(created_complaint.get('Attachment Link'), drive_link)
        print(f"[LIVE PASS] User lodged complaint {cid} with Google Drive Attachment Link: {drive_link}")

        # Step E: User verifies their complaint list
        res = session_user.get(f"{BASE_URL}/api/complaints", timeout=15)
        self.assertEqual(res.status_code, 200)
        user_complaints = res.json()['complaints']
        user_cids = [c['Complaint ID'] for c in user_complaints]
        self.assertIn(cid, user_cids)
        # Verify cross-user isolation: user cannot see other users' private info
        for c in user_complaints:
            self.assertEqual(c['Complainant Email'].lower().strip(), user_email.lower().strip())
        print(f"[LIVE PASS] User Complaints listing verified (Strict User Isolation enforced).")

        # Step F: Support Staff logs in and acts on complaint
        res = session_staff.post(f"{BASE_URL}/api/auth/login", json={'email': staff_email, 'password': staff_pw}, timeout=15)
        self.assertEqual(res.status_code, 200)
        staff_data = res.json()['user']
        self.assertEqual(staff_data['role'], 'Support Staff')
        print(f"[LIVE PASS] Support Staff logged in: {staff_email}")

        # Staff assigns complaint
        res = session_staff.post(f"{BASE_URL}/api/complaints/{cid}/assign", json={
            'assigned_to_email': staff_email,
            'assigned_to_name': staff_data['name']
        }, timeout=20)
        self.assertEqual(res.status_code, 200)
        print(f"[LIVE PASS] Support Staff assigned complaint {cid} to themselves.")

        # Staff posts Public Update
        res = session_staff.post(f"{BASE_URL}/api/complaints/{cid}/comments", json={
            'message': 'Technician has been dispatched to North entrance reader.',
            'comment_type': 'Public Update'
        }, timeout=20)
        self.assertEqual(res.status_code, 201)

        # Staff posts Internal Note
        res = session_staff.post(f"{BASE_URL}/api/complaints/{cid}/comments", json={
            'message': 'INTERNAL ONLY: Reader firmware v2.1 requires replacement next quarter.',
            'comment_type': 'Internal Note'
        }, timeout=20)
        self.assertEqual(res.status_code, 201)
        print(f"[LIVE PASS] Support Staff posted Public Update and Internal Note.")

        # Staff updates status
        res = session_staff.post(f"{BASE_URL}/api/complaints/{cid}/status", json={
            'status': 'In Progress',
            'remarks': 'Technician on-site.'
        }, timeout=20)
        self.assertEqual(res.status_code, 200)
        print(f"[LIVE PASS] Support Staff updated status to 'In Progress'.")

        # Step G: User checks complaint details -> Public Update visible, Internal Note hidden
        res = session_user.get(f"{BASE_URL}/api/complaints/{cid}", timeout=15)
        self.assertEqual(res.status_code, 200)
        user_view_data = res.json()
        comments_seen_by_user = user_view_data.get('comments', [])
        comment_types_seen = [c.get('Comment Type') for c in comments_seen_by_user]
        self.assertIn('Public Update', comment_types_seen)
        self.assertNotIn('Internal Note', comment_types_seen, "CRITICAL RBAC LEAK: Regular user saw Internal Note!")
        self.assertEqual(user_view_data['complaint']['Attachment Link'], drive_link)
        print(f"[LIVE PASS] User detail view verified: Public Update visible, Internal Note concealed.")

        # Step H: Role boundary checks (Security enforcement)
        # 1. User tries to create a department -> 403
        res = session_user.post(f"{BASE_URL}/api/departments", json={'name': 'Forbidden Dept'}, timeout=10)
        self.assertEqual(res.status_code, 403)
        # 2. User tries to list all users -> 403
        res = session_user.get(f"{BASE_URL}/api/users", timeout=10)
        self.assertEqual(res.status_code, 403)
        # 3. Support Staff tries to create a user -> 403
        res = session_staff.post(f"{BASE_URL}/api/users", json={
            'full_name': 'Illegal User',
            'email': 'illegal@example.com',
            'password': 'password',
            'role': 'User'
        }, timeout=10)
        self.assertEqual(res.status_code, 403)
        print(f"[LIVE PASS] RBAC endpoint boundaries strictly enforced (all unauthorized calls returned HTTP 403).")

        # Step I: Admin creates new department
        dept_name = f"Test Department {suffix}"
        res = session_admin.post(f"{BASE_URL}/api/departments", json={
            'name': dept_name,
            'description': 'Temporary automated test department',
            'lead_email': admin_email
        }, timeout=20)
        self.assertEqual(res.status_code, 201)
        print(f"[LIVE PASS] Admin created department: '{dept_name}'.")

        # Step J: Clean up live Google Sheets test records
        from services.google_client import get_gspread_client
        from config import Config
        gc = get_gspread_client()
        sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)

        # Clean Complaints tab
        ws_comp = sh.worksheet('Complaints')
        c_rows = ws_comp.get_all_values()
        for idx in range(len(c_rows), 1, -1):
            if c_rows[idx-1] and c_rows[idx-1][0] == cid:
                ws_comp.delete_rows(idx)
                print(f"[CLEANUP] Deleted test complaint '{cid}' from Complaints worksheet.")

        # Clean Status History tab
        ws_hist = sh.worksheet('Status History')
        h_rows = ws_hist.get_all_values()
        for idx in range(len(h_rows), 1, -1):
            if h_rows[idx-1] and len(h_rows[idx-1]) > 1 and h_rows[idx-1][1] == cid:
                ws_hist.delete_rows(idx)
        print(f"[CLEANUP] Deleted test status history for '{cid}'.")

        # Clean Assignments & Comments tab
        ws_comm = sh.worksheet('Assignments and Comments')
        m_rows = ws_comm.get_all_values()
        for idx in range(len(m_rows), 1, -1):
            if m_rows[idx-1] and len(m_rows[idx-1]) > 1 and m_rows[idx-1][1] == cid:
                ws_comm.delete_rows(idx)
        print(f"[CLEANUP] Deleted test comments for '{cid}'.")

        # Clean Users tab
        ws_users = sh.worksheet('Users')
        u_rows = ws_users.get_all_values()
        for idx in range(len(u_rows), 1, -1):
            if u_rows[idx-1] and len(u_rows[idx-1]) > 2 and u_rows[idx-1][2] in [staff_email, user_email]:
                ws_users.delete_rows(idx)
        print(f"[CLEANUP] Deleted test users '{staff_email}' and '{user_email}'.")

        # Clean Departments tab
        ws_dept = sh.worksheet('Departments')
        d_rows = ws_dept.get_all_values()
        for idx in range(len(d_rows), 1, -1):
            if d_rows[idx-1] and len(d_rows[idx-1]) > 1 and d_rows[idx-1][1] == dept_name:
                ws_dept.delete_rows(idx)
        print(f"[CLEANUP] Deleted test department '{dept_name}'.")

        print("[LIVE PASS] All test records cleanly purged. Existing production data remains intact.")

if __name__ == '__main__':
    unittest.main()
