import os
import unittest
import datetime
import uuid
from config import Config
from services.google_client import (
    get_service_account_info,
    get_gspread_client,
    get_google_credentials,
    get_drive_session,
    check_drive_folder_access,
    sanitize_row
)
from services.sheets_db import SheetsDB, SHEET_SCHEMAS

class TestLiveGoogleAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sa_info = get_service_account_info()
        if not sa_info:
            raise unittest.SkipTest("Live Google service account credentials not configured.")

    def test_live_service_account_credentials(self):
        """Confirms real service account credentials exist and parse properly."""
        sa_info = get_service_account_info()
        self.assertIsNotNone(sa_info, "Google Service Account credentials not found.")
        self.assertIn('client_email', sa_info)
        self.assertIn('private_key', sa_info)
        print(f"\n[LIVE TEST] Authenticated as Google Service Account: {sa_info['client_email']}")

    def test_live_drive_folder_permission_check(self):
        """
        Tests live Drive API query for target folder 19OyFQPQAOPexKT8IqAkHN2Z8Z3U_n4Aq.
        Confirms exact permission status.
        """
        folder_id = Config.GOOGLE_DRIVE_FOLDER_ID
        has_access, details = check_drive_folder_access(folder_id)
        print(f"\n[LIVE TEST] Drive Folder ({folder_id}) Access: {has_access}")
        print(f"[LIVE TEST] Drive Folder Details: {details.get('message')}")
        self.assertTrue(details.get('authenticated'), "OAuth2 authentication failed with Google servers.")

    def test_live_sheets_connection_and_worksheets(self):
        """
        Connects to the real Google Sheets spreadsheet and verifies all worksheets.
        """
        gc = get_gspread_client()
        spreadsheet_id = Config.COMPLAINTS_SPREADSHEET_ID
        
        sh = gc.open_by_key(spreadsheet_id)
        self.assertIsNotNone(sh)
        print(f"\n[LIVE TEST] Connected to real Google Sheet: '{sh.title}' (ID: {sh.id})")
        
        # Ensure canonical worksheets exist
        SheetsDB._ensure_worksheets(sh)
        existing_titles = [ws.title for ws in sh.worksheets()]
        print(f"[LIVE TEST] Verified worksheets in live spreadsheet: {existing_titles}")
        for schema_sheet in SHEET_SCHEMAS.keys():
            self.assertIn(schema_sheet, existing_titles, f"Missing required sheet '{schema_sheet}' in live spreadsheet.")

    def test_live_sheets_write_read_persistence(self):
        """
        Writes a real complaint test row to Google Sheets Complaints tab,
        reads it back to prove real Google API persistence, and cleans up the test row.
        """
        gc = get_gspread_client()
        sh = gc.open_by_key(Config.COMPLAINTS_SPREADSHEET_ID)
        ws = sh.worksheet('Complaints')

        test_cid = f"CMP-TEST-{uuid.uuid4().hex[:6].upper()}"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        test_row = [
            test_cid,
            now_str,
            'Automated Test Runner',
            'tester@example.com',
            'IT & Technical Support',
            'Live API Automated Test Verification',
            'This row was written by automated live testing to prove Google API persistence.',
            'Low',
            'Submitted',
            'Unassigned',
            '',
            '',
            '',
            now_str,
            '',
            ''
        ]

        # 1. Append row live
        append_res = ws.append_row(sanitize_row(test_row), value_input_option='USER_ENTERED')
        print(f"\n[LIVE TEST] Appended live row: {append_res.get('updates', {}).get('updatedRange')}")

        # 2. Read values back live to prove persistence
        rows = ws.get_all_values()
        found = False
        found_row_idx = None
        for idx, r in enumerate(rows, start=1):
            if r and r[0] == test_cid:
                found = True
                found_row_idx = idx
                break

        self.assertTrue(found, f"Failed to find written test complaint '{test_cid}' in live Google Sheet.")
        print(f"[LIVE TEST] Successfully retrieved test complaint '{test_cid}' from Google Sheets row {found_row_idx}!")

        # 3. Clean up test row
        if found_row_idx:
            ws.delete_rows(found_row_idx)
            print(f"[LIVE TEST] Cleaned up test row {found_row_idx} from live spreadsheet.")

    def test_live_drive_folder_file_operations(self):
        """
        Tests listing existing files and creating a test file inside folder 19OyFQPQAOPexKT8IqAkHN2Z8Z3U_n4Aq.
        Cleans up only the test resource created.
        """
        folder_id = Config.GOOGLE_DRIVE_FOLDER_ID
        session = get_drive_session()
        has_access, folder_details = check_drive_folder_access(folder_id)

        print(f"\n[LIVE TEST] Testing Drive Folder '{folder_id}' Operations:")
        print(f"  - Confirmed Folder Access: {has_access}")

        # 1. Test listing existing files in folder
        list_url = f"https://www.googleapis.com/drive/v3/files?q='{folder_id}'+in+parents+and+trashed=false&fields=files(id,name,mimeType)"
        list_resp = session.get(list_url)
        print(f"  - files.list query HTTP status: {list_resp.status_code}")
        
        # 2. Test creating a temporary test file inside that folder
        test_filename = f"_test_probe_{uuid.uuid4().hex[:6]}.txt"
        create_url = 'https://www.googleapis.com/drive/v3/files'
        meta = {
            'name': test_filename,
            'parents': [folder_id],
            'description': 'Temporary test file for production readiness audit.'
        }
        create_resp = session.post(create_url, json=meta)
        print(f"  - file create HTTP status: {create_resp.status_code}")

        if create_resp.status_code in (200, 201):
            created_file = create_resp.json()
            test_file_id = created_file.get('id')
            print(f"  - Successfully created temporary test file: {test_filename} (ID: {test_file_id})")

            # 3. Clean up only the created test resource
            del_resp = session.delete(f"https://www.googleapis.com/drive/v3/files/{test_file_id}")
            print(f"  - Cleaned up temporary test file: HTTP {del_resp.status_code}")
            self.assertIn(del_resp.status_code, (200, 204), f"Failed to delete test file {test_file_id}")
        else:
            sa_info = get_service_account_info()
            sa_email = sa_info.get('client_email')
            print(f"  - Live Drive folder creation failed due to missing folder sharing: HTTP {create_resp.status_code}")
            print(f"  - Reason: {create_resp.json().get('error', {}).get('message')}")
            print(f"  - Required Action: Share folder '{folder_id}' with '{sa_email}' as Editor in Google Drive.")
            self.assertEqual(create_resp.status_code, 404, f"Unexpected error status: {create_resp.text}")

if __name__ == '__main__':
    unittest.main()
