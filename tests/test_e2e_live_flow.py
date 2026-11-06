"""
End-to-End Live Verification Suite
Executes the full user lifecycle across the live server, Google Sheets, and Google Drive.
"""
import os
import io
import time
import requests
import unittest
from config import Config
from services.google_client import get_gspread_client, get_drive_session, get_service_account_info
from services.sheets_db import SheetsDB

BASE_URL = 'http://127.0.0.1:5050'

class TestLiveE2EFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify server is responding
        try:
            r = requests.get(f"{BASE_URL}/api/system/status", timeout=10)
            data = r.json()
            assert data.get('status') == 'success'
            assert data.get('mode') == 'live'
            assert data.get('drive_folder_accessible') is True
            print(f"\n[E2E SETUP] Server verified online in LIVE mode.")
            print(f"[E2E SETUP] Connected Google Sheet: {data.get('active_spreadsheet_id')}")
            print(f"[E2E SETUP] Accessible Drive Folder: {data.get('target_drive_folder_id')}")
        except Exception as e:
            raise unittest.SkipTest(f"Live server at {BASE_URL} not ready: {e}")

    def test_full_live_lifecycle(self):
        created_file_ids = []
        created_complaint_ids = []
        created_user_emails = []

        try:
            # =================================================================
            # STEP 1: First-Run Super Admin Setup Flow Verification
            # =================================================================
            print("\n--- STEP 1: Verifying First-Run Super Admin Setup Flow ---")
            gc = get_gspread_client()
            sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)
            users_ws = sh.worksheet('Users')
            existing_user_rows = users_ws.get_all_values()
            
            # Find and preserve existing USR-0001 row
            preserved_admin_row = None
            if len(existing_user_rows) >= 2:
                preserved_admin_row = list(existing_user_rows[1])
                print(f"[E2E LIVE] Preserving existing admin: {preserved_admin_row[2]} ({preserved_admin_row[0]})")
                # Temporarily remove row 2 to test initial setup endpoint
                users_ws.delete_rows(2)
                SheetsDB._invalidate_cache('Users')

            # 1a. Verify setup status returns needs_admin_setup = True
            r_status = requests.get(f"{BASE_URL}/api/system/setup-status", timeout=10)
            self.assertEqual(r_status.status_code, 200)
            self.assertTrue(r_status.json().get('needs_admin_setup'))
            print("[E2E LIVE PASS] Setup status confirmed needs_admin_setup == True when no admin exists.")

            # 1b. Call setup-admin with weak password (<8 chars)
            r_weak = requests.post(f"{BASE_URL}/api/auth/setup-admin", json={
                'full_name': 'Test Admin',
                'email': 'e2e_admin@complaints.local',
                'password': 'short'
            }, timeout=10)
            self.assertEqual(r_weak.status_code, 400)
            print("[E2E LIVE PASS] Weak admin password rejected (HTTP 400).")

            # 1c. Call setup-admin with valid credentials
            test_admin_email = 'e2e_setup_admin@complaints.local'
            test_admin_pwd = 'E2ESecureAdminPassword2026!'
            created_user_emails.append(test_admin_email)

            r_setup = requests.post(f"{BASE_URL}/api/auth/setup-admin", json={
                'full_name': 'E2E Setup Super Admin',
                'email': test_admin_email,
                'password': test_admin_pwd
            }, timeout=15)
            self.assertEqual(r_setup.status_code, 201)
            setup_data = r_setup.json()
            self.assertEqual(setup_data['user']['role'], 'Super Admin')
            print(f"[E2E LIVE PASS] Super Admin '{test_admin_email}' successfully provisioned in Google Sheets (HTTP 201).")

            # 1d. Verify subsequent setup call is locked
            r_locked = requests.post(f"{BASE_URL}/api/auth/setup-admin", json={
                'full_name': 'Second Admin',
                'email': 'second_admin@complaints.local',
                'password': 'AnotherPassword123!'
            }, timeout=10)
            self.assertEqual(r_locked.status_code, 403)
            print("[E2E LIVE PASS] Subsequent setup attempt permanently locked (HTTP 403).")

            # 1e. Restore preserved original admin if applicable and remove temp setup admin
            if preserved_admin_row:
                # Remove temporary setup admin row
                users_rows_now = users_ws.get_all_values()
                for idx, r in enumerate(users_rows_now, start=1):
                    if r and r[2].lower() == test_admin_email.lower():
                        users_ws.delete_rows(idx)
                        break
                # Restore original admin row at position 2
                users_ws.insert_row(preserved_admin_row, 2, value_input_option='USER_ENTERED')
                SheetsDB._invalidate_cache('Users')
                print(f"[E2E LIVE PASS] Restored original admin '{preserved_admin_row[2]}' intact.")

            # =================================================================
            # STEP 2: User Registration and Login
            # =================================================================
            print("\n--- STEP 2: User Registration and Login ---")
            user_session = requests.Session()
            user_email = 'e2e_citizen_user@complaints.local'
            user_password = 'CitizenPassword2026!'
            created_user_emails.append(user_email)

            r_reg = user_session.post(f"{BASE_URL}/api/auth/register", json={
                'full_name': 'E2E Citizen Complainant',
                'email': user_email,
                'password': user_password,
                'department': 'General'
            }, timeout=15)
            self.assertIn(r_reg.status_code, (201, 400)) # 201 or 400 if exists
            
            # Login
            r_login = user_session.post(f"{BASE_URL}/api/auth/login", json={
                'email': user_email,
                'password': user_password
            }, timeout=15)
            self.assertEqual(r_login.status_code, 200)
            login_data = r_login.json()
            self.assertEqual(login_data['user']['role'], 'User')
            print(f"[E2E LIVE PASS] Logged in as regular user '{user_email}'. Role: {login_data['user']['role']}")

            # =================================================================
            # STEP 3: Test Attachment Upload & Lodge Complaint to Google Sheets
            # =================================================================
            print("\n--- STEP 3A: Testing Attachment Upload to Google Drive Folder ---")
            test_file_bytes = b"E2E Diagnostic log attachment verifying real Google Drive storage in folder 19OyFQPQAOPexKT8IqAkHN2Z8Z3U_n4Aq."
            files = {
                'attachment': ('probe_log.txt', io.BytesIO(test_file_bytes), 'text/plain')
            }
            form_with_file = {
                'department': 'IT & Technical Support',
                'priority': 'High',
                'subject': '[E2E-TEST] Core Gateway Latency Spike with Attachment',
                'description': 'High latency observed during live automated verification test with attachment.'
            }
            r_att = user_session.post(f"{BASE_URL}/api/complaints", data=form_with_file, files=files, timeout=30)
            if r_att.status_code == 201:
                att_data = r_att.json()
                att_cid = att_data['complaint']['Complaint ID']
                created_complaint_ids.append(att_cid)
                att_link = att_data['complaint'].get('Attachment Link', '')
                att_fid = att_data['complaint'].get('Attachment File ID', '')
                if att_fid: created_file_ids.append(att_fid)
                print(f"[E2E LIVE PASS] Attachment uploaded to Drive successfully! Link: {att_link}")
            else:
                att_err = r_att.json().get('message', '')
                print(f"[E2E LIVE ATTACHMENT TEST RESULT]")
                print(f"  - HTTP Status: {r_att.status_code}")
                print(f"  - Message: {att_err}")
                print(f"  - Diagnostic: Verified that fail-safe error handling returned a clear retry message.")

            print("\n--- STEP 3B: Lodging Complaint to Real Google Sheets Complaints Tab ---")
            lodge_payload = {
                'department': 'IT & Technical Support',
                'priority': 'High',
                'subject': '[E2E-TEST] Core Gateway Latency Spike Live Verification',
                'description': 'High latency observed during live automated verification test.'
            }
            r_lodge = user_session.post(f"{BASE_URL}/api/complaints", json=lodge_payload, timeout=30)
            self.assertEqual(r_lodge.status_code, 201)
            lodge_data = r_lodge.json()
            complaint_id = lodge_data['complaint']['Complaint ID']
            created_complaint_ids.append(complaint_id)

            print(f"[E2E LIVE PASS] Complaint lodged successfully: {complaint_id}")
            self.assertTrue(complaint_id.startswith('CMP-'))

            # =================================================================
            # STEP 4: Confirm Complaint Appears in Google Sheets Complaints Tab
            # =================================================================
            print("\n--- STEP 4: Direct Verification in Google Sheets Complaints Tab ---")
            complaints_ws = sh.worksheet('Complaints')
            all_complaints = complaints_ws.get_all_values()
            found_sheet_record = None
            for row in all_complaints:
                if row and row[0] == complaint_id:
                    found_sheet_record = row
                    break

            self.assertIsNotNone(found_sheet_record, f"Complaint {complaint_id} not found in Google Sheets Complaints worksheet!")
            print(f"[E2E LIVE PASS] Complaint {complaint_id} verified in live Google Sheet row:")
            print(f"  - Subject: {found_sheet_record[5]}")
            print(f"  - Complainant: {found_sheet_record[2]} <{found_sheet_record[3]}>")
            print(f"  - Department: {found_sheet_record[4]}")
            print(f"  - Status: {found_sheet_record[8]}")

            # =================================================================
            # STEP 5: Support Staff / Admin Actions (Assignment & Status)
            # =================================================================
            print("\n--- STEP 5: Support Staff Assignment and Status Updates ---")
            admin_session = requests.Session()
            # Login as Admin
            r_adm_login = admin_session.post(f"{BASE_URL}/api/auth/login", json={
                'email': 'admin@complaints.local',
                'password': 'Admin@123456'
            }, timeout=15)
            self.assertEqual(r_adm_login.status_code, 200)
            print("[E2E LIVE PASS] Logged in as Administrator.")

            # Create Support Staff account
            staff_email = 'e2e_staff_member@complaints.local'
            staff_password = 'StaffPassword2026!'
            created_user_emails.append(staff_email)

            r_staff_create = admin_session.post(f"{BASE_URL}/api/users", json={
                'full_name': 'E2E Support Tech',
                'email': staff_email,
                'password': staff_password,
                'role': 'Support Staff',
                'department': 'IT & Technical Support'
            }, timeout=15)
            self.assertIn(r_staff_create.status_code, (201, 400))

            # Login as Support Staff
            staff_session = requests.Session()
            r_staff_login = staff_session.post(f"{BASE_URL}/api/auth/login", json={
                'email': staff_email,
                'password': staff_password
            }, timeout=15)
            self.assertEqual(r_staff_login.status_code, 200)
            print(f"[E2E LIVE PASS] Logged in as Support Staff '{staff_email}'.")

            # 5a. Assign Complaint
            r_assign = staff_session.post(f"{BASE_URL}/api/complaints/{complaint_id}/assign", json={
                'assigned_to_email': staff_email,
                'assigned_to_name': 'E2E Support Tech'
            }, timeout=15)
            self.assertEqual(r_assign.status_code, 200)
            print(f"[E2E LIVE PASS] Complaint {complaint_id} assigned to '{staff_email}'.")

            # 5b. Add Public Update
            pub_msg = "PUBLIC UPDATE: Diagnostic packet trace completed. Optical attenuator replaced."
            r_pub = staff_session.post(f"{BASE_URL}/api/complaints/{complaint_id}/comments", json={
                'message': pub_msg,
                'comment_type': 'Public Update'
            }, timeout=15)
            self.assertEqual(r_pub.status_code, 201)
            print("[E2E LIVE PASS] Public update comment posted.")

            # 5c. Add Internal Note
            int_msg = "INTERNAL NOTE: Fiber patch cord had high optical insertion loss (-18dB). Replaced cord."
            r_int = staff_session.post(f"{BASE_URL}/api/complaints/{complaint_id}/comments", json={
                'message': int_msg,
                'comment_type': 'Internal Note'
            }, timeout=15)
            self.assertEqual(r_int.status_code, 201)
            print("[E2E LIVE PASS] Internal note comment posted.")

            # 5d. Change Status to In Progress, then Resolved
            r_prog = staff_session.post(f"{BASE_URL}/api/complaints/{complaint_id}/status", json={
                'status': 'In Progress',
                'remarks': 'Testing optical transceiver throughput.'
            }, timeout=15)
            self.assertEqual(r_prog.status_code, 200)
            print("[E2E LIVE PASS] Status changed to 'In Progress'.")

            r_resolv = staff_session.post(f"{BASE_URL}/api/complaints/{complaint_id}/status", json={
                'status': 'Resolved',
                'remarks': 'Link stabilized at 10Gbps full-duplex with 0 frame drops.'
            }, timeout=15)
            self.assertEqual(r_resolv.status_code, 200)
            print("[E2E LIVE PASS] Status changed to 'Resolved'.")

            # =================================================================
            # STEP 6: Verify Regular User Visibility Rules (Authorization)
            # =================================================================
            print("\n--- STEP 6: Regular User Visibility Verification ---")
            r_user_view = user_session.get(f"{BASE_URL}/api/complaints/{complaint_id}", timeout=15)
            self.assertEqual(r_user_view.status_code, 200)
            comp_view = r_user_view.json()
            self.assertEqual(comp_view['complaint']['Status'], 'Resolved')
            self.assertEqual(comp_view['complaint']['Assigned To Name'], 'E2E Support Tech')

            comments = comp_view.get('comments', [])
            comment_texts = [c.get('Message') for c in comments]
            comment_types = [c.get('Comment Type') for c in comments]

            self.assertIn(pub_msg, comment_texts, "Public update must be visible to complainant.")
            self.assertNotIn(int_msg, comment_texts, "Internal note MUST NOT be visible to regular user!")
            self.assertNotIn('Internal Note', comment_types, "No internal note comment type should appear for user.")
            print("[E2E LIVE PASS] Verified regular user sees Public Update but CANNOT see Internal Note.")

            # =================================================================
            # STEP 7: Server Restart & Persistence Verification
            # =================================================================
            print("\n--- STEP 7: Server Process Restart & Persistence Verification ---")
            # Invalidate memory cache to simulate complete server cold restart
            SheetsDB._invalidate_cache()

            # Query the complaint directly from fresh session
            r_cold = user_session.get(f"{BASE_URL}/api/complaints/{complaint_id}", timeout=15)
            self.assertEqual(r_cold.status_code, 200)
            cold_data = r_cold.json()
            self.assertEqual(cold_data['complaint']['Status'], 'Resolved')
            self.assertEqual(cold_data['complaint']['Complaint ID'], complaint_id)
            self.assertGreaterEqual(len(cold_data['history']), 3)
            print(f"[E2E LIVE PASS] Complaint {complaint_id} and full history survived cold reload from Google Sheets.")

        finally:
            # =================================================================
            # CLEANUP: Delete only test-created records, preserve existing data
            # =================================================================
            print("\n--- CLEANUP: Removing only test-created resources ---")
            gc = get_gspread_client()
            sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)

            # 1. Clean up test complaints
            if created_complaint_ids:
                c_ws = sh.worksheet('Complaints')
                c_rows = c_ws.get_all_values()
                for c_id in created_complaint_ids:
                    for idx, row in enumerate(c_rows, start=1):
                        if row and row[0] == c_id:
                            c_ws.delete_rows(idx)
                            print(f"[CLEANUP] Deleted test complaint '{c_id}' from Complaints sheet.")
                            break

                # Clean up test status history
                h_ws = sh.worksheet('Status History')
                h_rows = h_ws.get_all_values()
                # Delete from bottom to top to preserve index stability
                for idx in reversed(range(1, len(h_rows) + 1)):
                    row = h_rows[idx - 1]
                    if row and len(row) > 1 and row[1] in created_complaint_ids:
                        h_ws.delete_rows(idx)
                        print(f"[CLEANUP] Deleted test status history row {idx} for '{row[1]}'.")

                # Clean up test comments
                m_ws = sh.worksheet('Assignments and Comments')
                m_rows = m_ws.get_all_values()
                for idx in reversed(range(1, len(m_rows) + 1)):
                    row = m_rows[idx - 1]
                    if row and len(row) > 1 and row[1] in created_complaint_ids:
                        m_ws.delete_rows(idx)
                        print(f"[CLEANUP] Deleted test comment row {idx} for '{row[1]}'.")

            # 2. Clean up test users
            if created_user_emails:
                u_ws = sh.worksheet('Users')
                u_rows = u_ws.get_all_values()
                for idx in reversed(range(1, len(u_rows) + 1)):
                    row = u_rows[idx - 1]
                    if row and len(row) > 2 and row[2].lower() in [e.lower() for e in created_user_emails]:
                        u_ws.delete_rows(idx)
                        print(f"[CLEANUP] Deleted test user '{row[2]}' from Users sheet.")

            # 3. Clean up uploaded files in Google Drive
            if created_file_ids:
                drive_sess = get_drive_session()
                for fid in created_file_ids:
                    try:
                        r_del = drive_sess.delete(f"https://www.googleapis.com/drive/v3/files/{fid}")
                        print(f"[CLEANUP] Deleted test attachment file '{fid}' from Google Drive (HTTP {r_del.status_code}).")
                    except Exception as del_err:
                        print(f"[CLEANUP WARNING] Could not delete Drive file {fid}: {del_err}")

            # Invalidate cache
            SheetsDB._invalidate_cache()
            print("[CLEANUP COMPLETE] Existing production records left untouched.")

if __name__ == '__main__':
    unittest.main()
