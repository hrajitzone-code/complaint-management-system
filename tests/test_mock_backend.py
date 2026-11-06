import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import unittest
from config import Config
from app import app
from services.mock_sheets_db import MockSheetsDB
from services.google_client import sanitize_cell_value

class TestMockComplaintSystem(unittest.TestCase):
    def setUp(self):
        os.environ['TESTING'] = 'True'
        os.environ['USE_MOCK_STORAGE'] = 'True'
        app.config['TESTING'] = True
        Config.INITIAL_ADMIN_PASSWORD = 'Admin@123456'
        MockSheetsDB.reset()
        self.client = app.test_client()

    def test_formula_injection_defense(self):
        """Verifies that cells starting with dangerous triggers are sanitized."""
        self.assertEqual(sanitize_cell_value("=SUM(A1:A10)"), "'=SUM(A1:A10)")
        self.assertEqual(sanitize_cell_value("+1234567890"), "'+1234567890")
        self.assertEqual(sanitize_cell_value("-100.50"), "'-100.50")
        self.assertEqual(sanitize_cell_value("@cmd"), "'@cmd")
        self.assertEqual(sanitize_cell_value("\tmalicious"), "'\tmalicious")
        self.assertEqual(sanitize_cell_value("Normal text"), "Normal text")
        self.assertEqual(sanitize_cell_value(""), "")
        self.assertEqual(sanitize_cell_value(None), "")

    def test_first_run_admin_provisioning(self):
        """Verifies secure first-run admin provisioning and subsequent locking."""
        # Reset DB with NO admin
        Config.INITIAL_ADMIN_PASSWORD = None
        MockSheetsDB.reset()
        self.assertFalse(MockSheetsDB.has_admin_user())

        # 1. Check setup status -> needs admin setup
        res_status = self.client.get('/api/system/setup-status')
        self.assertEqual(res_status.status_code, 200)
        self.assertTrue(res_status.get_json()['needs_admin_setup'])

        # 2. Reject weak admin password (< 8 chars)
        res_weak = self.client.post('/api/auth/setup-admin', json={
            'full_name': 'First Admin',
            'email': 'first_admin@example.com',
            'password': 'short'
        })
        self.assertEqual(res_weak.status_code, 400)
        self.assertIn('8 characters', res_weak.get_json()['message'])

        # 3. Successful admin provisioning
        res_ok = self.client.post('/api/auth/setup-admin', json={
            'full_name': 'Chief Administrator',
            'email': 'chief_admin@organization.local',
            'password': 'StrongSecureAdminPassword2026!'
        })
        self.assertEqual(res_ok.status_code, 201)
        data = res_ok.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['user']['role'], 'Super Admin')
        self.assertEqual(data['user']['email'], 'chief_admin@organization.local')

        # 4. Check setup status -> now has admin, needs_admin_setup is False
        res_status2 = self.client.get('/api/system/setup-status')
        self.assertFalse(res_status2.get_json()['needs_admin_setup'])
        self.assertTrue(res_status2.get_json()['has_admin'])

        # 5. Subsequent attempts to provision admin must be locked (403)
        res_locked = self.client.post('/api/auth/setup-admin', json={
            'full_name': 'Hacker',
            'email': 'hacker@example.com',
            'password': 'AnotherPassword123!'
        })
        self.assertEqual(res_locked.status_code, 403)
        self.assertIn('locked', res_locked.get_json()['message'].lower())

    def test_bootstrap_admin_login(self):
        """Verifies default admin bootstrap and PBKDF2 password authentication."""
        # Test wrong password
        res = self.client.post('/api/auth/login', json={'email': 'admin@complaints.local', 'password': 'WrongPassword!'})
        self.assertEqual(res.status_code, 401)

        # Test correct password
        res = self.client.post('/api/auth/login', json={'email': 'admin@complaints.local', 'password': 'Admin@123456'})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['user']['role'], 'Super Admin')
        self.assertEqual(data['user']['email'], 'admin@complaints.local')

    def test_user_registration_and_login(self):
        """Verifies user self-registration and subsequent login."""
        reg_payload = {
            'full_name': 'Alice Smith',
            'email': 'alice@example.com',
            'password': 'SecureAlicePassword123',
            'department': 'General'
        }
        res_reg = self.client.post('/api/auth/register', json=reg_payload)
        self.assertEqual(res_reg.status_code, 201)

        # Login with new account
        res_login = self.client.post('/api/auth/login', json={
            'email': 'alice@example.com',
            'password': 'SecureAlicePassword123'
        })
        self.assertEqual(res_login.status_code, 200)
        user = res_login.get_json()['user']
        self.assertEqual(user['name'], 'Alice Smith')
        self.assertEqual(user['role'], 'User')

    def test_lodge_complaint_and_unique_id(self):
        """Verifies complaint submission, unique ID format, and initial history."""
        # Login as Alice
        self.client.post('/api/auth/register', json={
            'full_name': 'Alice Smith', 'email': 'alice@example.com',
            'password': 'Password123', 'department': 'General'
        })
        self.client.post('/api/auth/login', json={'email': 'alice@example.com', 'password': 'Password123'})

        complaint_payload = {
            'department': 'IT & Technical Support',
            'priority': 'High',
            'subject': 'VPN Connection dropped repeatedly',
            'description': 'Cannot access internal git repositories since 9 AM.'
        }
        res = self.client.post('/api/complaints', json=complaint_payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertIn('complaint', data)
        comp = data['complaint']
        
        # ID format check: CMP-YYYYMMDD-XXXX
        self.assertTrue(comp['Complaint ID'].startswith('CMP-'))
        self.assertEqual(comp['Status'], 'Submitted')
        self.assertEqual(comp['Priority'], 'High')
        self.assertEqual(comp['Complainant Email'], 'alice@example.com')

    def test_duplicate_submission_debounce(self):
        """Verifies duplicate submission prevention within debounce window."""
        self.client.post('/api/auth/register', json={
            'full_name': 'Alice Smith', 'email': 'alice@example.com',
            'password': 'Password123', 'department': 'General'
        })
        self.client.post('/api/auth/login', json={'email': 'alice@example.com', 'password': 'Password123'})

        complaint_payload = {
            'department': 'Finance & Billing',
            'priority': 'Low',
            'subject': 'Duplicate Invoice Question',
            'description': 'Details regarding reimbursement reference #44921.'
        }

        # First submission succeeds
        res1 = self.client.post('/api/complaints', json=complaint_payload)
        self.assertEqual(res1.status_code, 201)

        # Immediate duplicate submission must be rejected with 409 Conflict
        res2 = self.client.post('/api/complaints', json=complaint_payload)
        self.assertEqual(res2.status_code, 409)
        self.assertIn('duplicate', res2.get_json()['message'].lower())

    def test_user_isolation_and_idor_protection(self):
        """
        CRITICAL SECURITY TEST:
        Verifies that a regular user CANNOT view other users' complaints by ID.
        """
        # 1. Register Alice and lodge complaint
        self.client.post('/api/auth/register', json={'full_name': 'Alice', 'email': 'alice@example.com', 'password': 'AlicePassword123', 'department': 'IT'})
        self.client.post('/api/auth/login', json={'email': 'alice@example.com', 'password': 'AlicePassword123'})
        res_alice = self.client.post('/api/complaints', json={'department': 'IT', 'priority': 'Medium', 'subject': 'Alice Secret Issue', 'description': 'Private details'})
        alice_cid = res_alice.get_json()['complaint']['Complaint ID']

        # 2. Register Bob
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/register', json={'full_name': 'Bob', 'email': 'bob@example.com', 'password': 'BobPassword123', 'department': 'HR'})
        self.client.post('/api/auth/login', json={'email': 'bob@example.com', 'password': 'BobPassword123'})

        # Bob lists his complaints: should be 0
        res_bobs_list = self.client.get('/api/complaints')
        self.assertEqual(len(res_bobs_list.get_json()['complaints']), 0)

        # Bob attempts to access Alice's complaint directly by ID -> MUST RETURN 403 FORBIDDEN
        res_idor = self.client.get(f'/api/complaints/{alice_cid}')
        self.assertEqual(res_idor.status_code, 403)
        self.assertIn('forbidden', res_idor.get_json()['message'].lower())

        # Admin CAN access Alice's complaint
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login', json={'email': 'admin@complaints.local', 'password': 'Admin@123456'})
        res_admin = self.client.get(f'/api/complaints/{alice_cid}')
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.get_json()['complaint']['Subject'], 'Alice Secret Issue')

    def test_staff_status_lifecycle_and_assignment(self):
        """Verifies staff assignment, status changes, and history timeline generation."""
        # 1. Create complaint as Alice
        self.client.post('/api/auth/register', json={'full_name': 'Alice', 'email': 'alice@example.com', 'password': 'AlicePassword123', 'department': 'Facilities'})
        self.client.post('/api/auth/login', json={'email': 'alice@example.com', 'password': 'AlicePassword123'})
        res = self.client.post('/api/complaints', json={'department': 'Facilities', 'priority': 'Urgent', 'subject': 'AC Leakage', 'description': 'Server room AC dripping'})
        cid = res.get_json()['complaint']['Complaint ID']

        # 2. Login as Admin
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login', json={'email': 'admin@complaints.local', 'password': 'Admin@123456'})

        # Assign complaint
        res_assign = self.client.post(f'/api/complaints/{cid}/assign', json={
            'assigned_to_email': 'technician@example.com',
            'assigned_to_name': 'Bob Tech'
        })
        self.assertEqual(res_assign.status_code, 200)

        # Update status to In Progress
        res_prog = self.client.post(f'/api/complaints/{cid}/status', json={
            'status': 'In Progress',
            'remarks': 'Technician dispatched with replacement pipe.'
        })
        self.assertEqual(res_prog.status_code, 200)

        # Resolve complaint
        res_res = self.client.post(f'/api/complaints/{cid}/status', json={
            'status': 'Resolved',
            'remarks': 'Pipe replaced and temperature stabilized.'
        })
        self.assertEqual(res_res.status_code, 200)

        # Check complaint history
        details = self.client.get(f'/api/complaints/{cid}').get_json()
        self.assertEqual(details['complaint']['Status'], 'Resolved')
        self.assertEqual(details['complaint']['Assigned To Name'], 'Bob Tech')
        self.assertNotEqual(details['complaint']['Resolved At'], '')
        
        # History must show all status changes
        history = details['history']
        self.assertGreaterEqual(len(history), 4)
        statuses = [h['New Status'] for h in history]
        self.assertIn('Submitted', statuses)
        self.assertIn('In Progress', statuses)
        self.assertIn('Resolved', statuses)

    def test_comments_visibility_rules(self):
        """Verifies that internal staff notes are hidden from regular users."""
        self.client.post('/api/auth/register', json={'full_name': 'Carol', 'email': 'carol@example.com', 'password': 'CarolPassword123', 'department': 'Finance'})
        self.client.post('/api/auth/login', json={'email': 'carol@example.com', 'password': 'CarolPassword123'})
        res_carol = self.client.post('/api/complaints', json={'department': 'Finance', 'priority': 'Low', 'subject': 'Carol Issue', 'description': 'Carol text'})
        carol_cid = res_carol.get_json()['complaint']['Complaint ID']

        # Admin adds internal note to Carol's complaint
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login', json={'email': 'admin@complaints.local', 'password': 'Admin@123456'})
        self.client.post(f'/api/complaints/{carol_cid}/comments', json={'message': 'Secret staff observation.', 'comment_type': 'Internal Note'})
        self.client.post(f'/api/complaints/{carol_cid}/comments', json={'message': 'Public update for Carol.', 'comment_type': 'Public Update'})

        # Carol reads her complaint: must NOT see the Internal Note
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login', json={'email': 'carol@example.com', 'password': 'CarolPassword123'})
        carol_view = self.client.get(f'/api/complaints/{carol_cid}').get_json()
        comment_types = [c['Comment Type'] for c in carol_view['comments']]
        self.assertNotIn('Internal Note', comment_types)
        self.assertIn('Public Update', comment_types)

if __name__ == '__main__':
    unittest.main()
