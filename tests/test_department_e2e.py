import requests
import json
import sys
import time

BASE_URL = "http://127.0.0.1:5050"
ADMIN_EMAIL = "admin@complaints.local"
ADMIN_PASSWORD = "Admin@123456"

session = requests.Session()

def run_tests():
    print("=== STARTING DEPARTMENT & MULTI-SELECT E2E TESTS ===")

    # 1. Login as Super Admin
    login_res = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    user = login_res.json()["user"]
    print(f" [PASS] Authenticated as {user['email']} ({user['role']})")

    # 2. List Departments
    depts_res = session.get(f"{BASE_URL}/api/departments?all=true")
    assert depts_res.status_code == 200, f"List departments failed: {depts_res.text}"
    depts = depts_res.json()["departments"]
    print(f" [PASS] Found {len(depts)} departments:")
    for d in depts:
        print(f"   - {d.get('Department ID')}: {d.get('Name')}")

    # Clean up any leftover test departments from previous partial runs
    for d in depts:
        if "QA Automation" in d.get("Name", ""):
            print(f" Cleaning up leftover test department: {d.get('Department ID')} ({d.get('Name')})")
            session.delete(f"{BASE_URL}/api/departments/{d.get('Department ID')}", json={"reassign_to": "Customer Operations"})

    # 3. Create a New Department with unique name
    ts = int(time.time())
    test_dept_name = f"QA Automation {ts}"
    create_res = session.post(f"{BASE_URL}/api/departments", json={
        "name": test_dept_name,
        "description": "Automated Quality Assurance Department for E2E tests",
        "lead_email": "qa-lead@complaints.local"
    })
    assert create_res.status_code in [200, 201], f"Create department failed: {create_res.text}"
    test_dept = create_res.json().get("department", {})
    test_dept_id = test_dept.get("Department ID")
    print(f" [PASS] Created Department: {test_dept_name} (ID: {test_dept_id})")

    # 4. Fetch Department by ID
    get_res = session.get(f"{BASE_URL}/api/departments/{test_dept_id}")
    assert get_res.status_code == 200, f"Get dept failed: {get_res.text}"
    assert get_res.json()["department"]["Name"] == test_dept_name
    print(f" [PASS] Successfully fetched department by ID: {test_dept_id}")

    # 5. Check Initial Usage
    usage_res = session.get(f"{BASE_URL}/api/departments/{test_dept_id}/usage")
    assert usage_res.status_code == 200, f"Check usage failed: {usage_res.text}"
    usage_data = usage_res.json()
    assert usage_data["usage_count"] == 0, f"Expected 0 usage, got {usage_data['usage_count']}"
    assert usage_data["can_delete_without_reassign"] is True
    print(f" [PASS] Initial usage is 0, can_delete_without_reassign=True")

    # 6. Lodge Complaint with MULTIPLE departments: [test_dept_name, 'Finance & Billing']
    multi_dept_selection = f"{test_dept_name}, Finance & Billing"
    complaint_payload = {
        "customer_name": "Test Customer E2E",
        "customer_mobile_no": "+91 99999 88888",
        "invoice_no": "INV-MULTI-DEPT-001",
        "complain_date": "2026-11-02",
        "employee_name": "Tester Bot",
        "salesperson_name": "Sales Rep 1",
        "department_name": multi_dept_selection,
        "complain_types": "Multi-Department Test Issue",
        "priority": "High",
        "category": "Integration",
        "vendor_name": "Acme Corp",
        "complain_details": "Verifying multi-department selection persists and functions correctly.",
        "remarks": "E2E testing remarks",
        "status": "Submitted"
    }
    lodge_res = session.post(f"{BASE_URL}/api/complaints", data=complaint_payload)
    assert lodge_res.status_code in [200, 201], f"Lodge complaint failed: {lodge_res.text}"
    comp_data = lodge_res.json()
    test_comp_id = comp_data["complaint"]["Complaint ID"]
    saved_dept = comp_data["complaint"].get("Department Name") or comp_data["complaint"].get("Department")
    print(f" [PASS] Lodged Complaint {test_comp_id} with multiple departments: '{saved_dept}'")
    assert test_dept_name in saved_dept and "Finance & Billing" in saved_dept, f"Saved dept unexpected: {saved_dept}"

    # 7. Check Department Usage Now
    usage_res2 = session.get(f"{BASE_URL}/api/departments/{test_dept_id}/usage")
    assert usage_res2.status_code == 200
    usage_data2 = usage_res2.json()
    assert usage_data2["usage_count"] == 1, f"Expected 1 linked complaint, got {usage_data2['usage_count']}"
    assert test_comp_id in usage_data2["complaint_ids"]
    assert usage_data2["can_delete_without_reassign"] is False
    print(f" [PASS] Department usage correctly reflects 1 linked complaint: {usage_data2['complaint_ids']}")

    # 8. Attempt Delete WITHOUT Reassignment -> MUST Fail with 409 Conflict
    del_fail_res = session.delete(f"{BASE_URL}/api/departments/{test_dept_id}")
    assert del_fail_res.status_code == 409, f"Expected 409 Conflict, got {del_fail_res.status_code}: {del_fail_res.text}"
    print(f" [PASS] Deletion without reassignment properly rejected (409): {del_fail_res.json().get('message')}")

    # 9. Edit the Department Name -> Verify Auto-Renaming across complaints
    updated_dept_name = "QA Automation & Engineering"
    put_res = session.put(f"{BASE_URL}/api/departments/{test_dept_id}", json={
        "name": updated_dept_name,
        "description": "Updated QA department description",
        "lead_email": "qa-lead-updated@complaints.local",
        "is_active": "TRUE"
    })
    assert put_res.status_code == 200, f"Update dept failed: {put_res.text}"
    print(f" [PASS] Renamed department to '{updated_dept_name}'")

    # Verify complaint in Google Sheets was auto-updated to new department name
    comp_check_res = session.get(f"{BASE_URL}/api/complaints/{test_comp_id}")
    assert comp_check_res.status_code == 200
    comp_dept_after_rename = comp_check_res.json()["complaint"].get("Department Name")
    print(f" [PASS] Complaint department string after auto-rename: '{comp_dept_after_rename}'")
    assert updated_dept_name in comp_dept_after_rename, f"Expected '{updated_dept_name}' in '{comp_dept_after_rename}'"

    # 10. Delete Department WITH Reassignment to 'Customer Operations'
    del_success_res = session.delete(
        f"{BASE_URL}/api/departments/{test_dept_id}",
        json={"reassign_to": "Customer Operations"}
    )
    assert del_success_res.status_code == 200, f"Delete with reassign failed: {del_success_res.text}"
    print(f" [PASS] Successfully deleted department with reassignment: {del_success_res.json().get('message')}")

    # Verify complaint was updated with replacement department
    comp_check_res2 = session.get(f"{BASE_URL}/api/complaints/{test_comp_id}")
    comp_dept_after_del = comp_check_res2.json()["complaint"].get("Department Name")
    print(f" [PASS] Complaint department string after reassignment: '{comp_dept_after_del}'")
    assert "Customer Operations" in comp_dept_after_del
    assert updated_dept_name not in comp_dept_after_del

    # 11. Clean Up Test Complaint
    del_comp_res = session.delete(f"{BASE_URL}/api/complaints/{test_comp_id}")
    assert del_comp_res.status_code == 200, f"Cleanup test complaint failed: {del_comp_res.text}"
    print(f" [PASS] Cleaned up test complaint {test_comp_id}")

    # 12. Verify Production Records Preserved
    all_res = session.get(f"{BASE_URL}/api/complaints")
    assert all_res.status_code == 200
    all_ids = [c.get("Complaint ID") for c in all_res.json()["complaints"]]
    assert "CMP-20261025-B5D6" in all_ids, "Production record CMP-20261025-B5D6 is missing!"
    assert "CMP-20261030-09DA" in all_ids, "Production record CMP-20261030-09DA is missing!"
    print(f" [PASS] Verified production complaints CMP-20261025-B5D6 & CMP-20261030-09DA are intact.")

    print("\n=== ALL E2E VERIFICATION CHECKS PASSED SUCCESSFULLY (100%) ===")

if __name__ == "__main__":
    try:
        run_tests()
    except Exception as e:
        print(f"\n[FAIL] Exception during test execution: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
