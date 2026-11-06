import sys
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from services.sheets_db import SheetsDB

def run_live_crud_test():
    print("=== STEP 1: Verify Existing Production Records in Google Sheets ===")
    existing = SheetsDB.get_complaints_for_user('admin@complaints.local', role='Super Admin')
    print(f"Total existing complaints in live Google Sheet: {len(existing)}")
    prod_ids = [c['Complaint ID'] for c in existing]
    print("Existing IDs in sheet:", prod_ids)
    assert 'CMP-20261025-B5D6' in prod_ids, "Production row 1 (CMP-20261025-B5D6) missing!"
    assert 'CMP-20261030-09DA' in prod_ids, "Production row 2 (CMP-20261030-09DA) missing!"
    print("Production rows verified intact.\n")

    print("=== STEP 2: CREATE Test Complaint With All 21 Fields in Google Sheets ===")
    new_c = SheetsDB.lodge_complaint(
        employee_name='Aarav Mehta',
        complain_date='2026-11-02',
        customer_name='Rohit Sharma',
        invoice_no='INV-LIVE-777',
        complain_types='Transit Damage',
        priority='High',
        remarks='Outer seal broken',
        complain_details='Package dropped during delivery, corner crushed',
        customer_mobile_no='9876543210',
        department_name='Customer Operations',
        category='Logistics',
        vendor_name='FastShip Logistics',
        salesperson_name='Vikram Rao',
        status='Submitted',
        closing_date='',
        solution_given='',
        handled_by='Unassigned',
        total_time_taken='',
        comments='Customer sent photos via email',
        gr_amount='3499.50',
        gr_expected_date='2026-11-08',
        attachment_link='https://drive.google.com/file/d/test-live-proof',
        complainant_email='admin@complaints.local'
    )
    test_cid = new_c['Complaint ID']
    print(f"Created Live Complaint ID: {test_cid}")
    assert new_c['Customer Name'] == 'Rohit Sharma'
    assert new_c['Invoice No.'] == 'INV-LIVE-777'
    assert new_c['GR Amount'] == '3499.50'
    print("CREATE: PASS\n")

    print("=== STEP 3: READ / VIEW Test Record from Live Google Sheets ===")
    SheetsDB._invalidate_cache()
    fetched = SheetsDB.get_complaint_by_id(test_cid)
    assert fetched is not None, "Record not found in live sheet!"
    print("Fetched Customer Name:", fetched.get('Customer Name'))
    print("Fetched Complain Types:", fetched.get('Complain Types'))
    print("Fetched Vendor Name:", fetched.get('Vendor Name'))
    print("Fetched GR Amount:", fetched.get('GR Amount'))
    print("Fetched Attachment Link:", fetched.get('Attachment Link'))
    assert fetched.get('Customer Name') == 'Rohit Sharma'
    assert fetched.get('Complain Types') == 'Transit Damage'
    assert fetched.get('Vendor Name') == 'FastShip Logistics'
    assert fetched.get('Invoice No.') == 'INV-LIVE-777'
    print("READ: PASS\n")

    print("=== STEP 4: UPDATE / EDIT Test Record in Live Google Sheets ===")
    admin_user = {'name': 'System Administrator', 'email': 'admin@complaints.local', 'role': 'Super Admin'}
    updated = SheetsDB.update_complaint(test_cid, {
        'Status': 'Resolved',
        'Closing Date': '2026-11-05',
        'Solution Given': 'Approved full replacement and collected damaged item',
        'Handled By': 'Senior Support Officer',
        'remarks': 'Replacement processed successfully'
    }, admin_user)
    print("Updated Status:", updated.get('Status'))
    print("Updated Closing Date:", updated.get('Closing Date'))
    print("Updated Solution Given:", updated.get('Solution Given'))
    print("Updated Handled By:", updated.get('Handled By'))
    print("Auto Total Time Taken:", updated.get('Total Time Taken'))
    assert updated.get('Status') == 'Resolved'
    assert updated.get('Total Time Taken') == '3 days'
    assert updated.get('Handled By') == 'Senior Support Officer'
    print("UPDATE: PASS\n")

    print("=== STEP 5: DELETE Test Record from Live Google Sheets ===")
    del_res = SheetsDB.delete_complaint(test_cid, admin_user)
    assert del_res is True
    SheetsDB._invalidate_cache()
    check_del = SheetsDB.get_complaint_by_id(test_cid)
    assert check_del is None, "Complaint should be deleted from Google Sheets!"
    print("DELETE: PASS\n")

    print("=== STEP 6: Verify Status History Audit Log ===")
    history = SheetsDB.get_status_history(test_cid)
    print(f"Status History count for {test_cid}: {len(history)}")
    for h in history:
        print(f"  {h.get('Timestamp')}: {h.get('Old Status')} -> {h.get('New Status')} by {h.get('Changed By Name')}")
    assert any(h.get('New Status') == 'DELETED' for h in history), "DELETED audit log missing!"
    print("AUDIT TRAIL: PASS\n")

    print("=== STEP 7: Verify Production Rows Preserved ===")
    remaining = SheetsDB.get_complaints_for_user('admin@complaints.local', role='Super Admin')
    remaining_ids = [c['Complaint ID'] for c in remaining]
    print("Remaining IDs in live sheet:", remaining_ids)
    assert 'CMP-20261025-B5D6' in remaining_ids, "Production row 1 altered or removed!"
    assert 'CMP-20261030-09DA' in remaining_ids, "Production row 2 altered or removed!"
    assert test_cid not in remaining_ids, "Test record was not deleted!"
    print("PRODUCTION PRESERVATION: PASS\n")

    print("**************************************************")
    print("LIVE GOOGLE SHEETS FULL CRUD TEST: 100% SUCCESSFUL!")
    print("**************************************************")

if __name__ == '__main__':
    run_live_crud_test()
