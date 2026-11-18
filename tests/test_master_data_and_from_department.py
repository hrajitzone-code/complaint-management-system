import os
import sys
import unittest
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ['TESTING'] = 'True'

from app import app
from services.mock_sheets_db import MockSheetsDB

class TestMasterDataAndFromDepartment(unittest.TestCase):
    def setUp(self):
        MockSheetsDB.reset()
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        # Seed authenticated users
        MockSheetsDB._store['Users'] = [
            {
                'User ID': 'USR-0001',
                'Full Name': 'Super Admin One',
                'Email': 'superadmin@example.com',
                'Password Hash': 'dummyhash',
                'Role': 'Super Admin',
                'Department': 'Executive',
                'Is Active': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Login': ''
            },
            {
                'User ID': 'USR-0002',
                'Full Name': 'Regular User',
                'Email': 'user@example.com',
                'Password Hash': 'dummyhash',
                'Role': 'User',
                'Department': 'Sales',
                'Is Active': 'TRUE',
                'Created At': '2026-01-01 00:00:00',
                'Last Login': ''
            }
        ]

    def _login_as(self, email, role='User', name='Test User'):
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'id': 'USR-TEST',
                'email': email,
                'name': name,
                'role': role,
                'department': 'General'
            }

    # =========================================================================
    # 1. Master Options API Tests
    # =========================================================================

    def test_get_master_options(self):
        """Verify /api/master/options returns all required dropdown choices."""
        self._login_as('user@example.com', 'User', 'Regular User')
        res = self.client.get('/api/master/options')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('status'), 'success')
        self.assertIn('complaint_types', data)
        self.assertIn('salespersons', data)
        self.assertIn('departments', data)
        self.assertIn('from_departments', data)
        self.assertEqual(data['from_departments'], ['Online', 'Offline', 'Packing', 'None'])
        self.assertTrue(len(data['complaint_types']) > 0)
        self.assertTrue(len(data['salespersons']) > 0)

    # =========================================================================
    # 2. Complaint Types CRUD & Validation
    # =========================================================================

    def test_complaint_types_admin_crud(self):
        """Admin can Add, Edit, Activate, Deactivate, and Delete complaint types."""
        self._login_as('superadmin@example.com', 'Super Admin', 'Super Admin One')

        # 1. Create a new complaint type
        create_res = self.client.post('/api/master/complaint-types', json={
            'name': 'Fabric Bleeding',
            'description': 'Color bleeds after first wash or cleaning'
        })
        self.assertEqual(create_res.status_code, 201)
        ct_data = create_res.get_json().get('complaint_type')
        ct_id = ct_data['Type ID']
        self.assertEqual(ct_data['Name'], 'Fabric Bleeding')

        # 2. Duplicate creation prevention
        dup_res = self.client.post('/api/master/complaint-types', json={
            'name': 'Fabric Bleeding',
            'description': 'Duplicate attempt'
        })
        self.assertEqual(dup_res.status_code, 400)
        self.assertIn('already exists', dup_res.get_json().get('message', ''))

        # 3. Update complaint type details
        upd_res = self.client.put(f'/api/master/complaint-types/{ct_id}', json={
            'name': 'Fabric Bleeding & Fading',
            'description': 'Color bleeds or fades after washing',
            'is_active': 'TRUE'
        })
        self.assertEqual(upd_res.status_code, 200)
        self.assertEqual(upd_res.get_json()['complaint_type']['Name'], 'Fabric Bleeding & Fading')

        # 4. Deactivate complaint type
        deact_res = self.client.put(f'/api/master/complaint-types/{ct_id}', json={
            'name': 'Fabric Bleeding & Fading',
            'description': 'Color bleeds or fades after washing',
            'is_active': 'FALSE'
        })
        self.assertEqual(deact_res.status_code, 200)
        self.assertEqual(deact_res.get_json()['complaint_type']['Is Active'], 'FALSE')

        # Verify inactive type is excluded from active options
        opt_res = self.client.get('/api/master/options')
        self.assertNotIn('Fabric Bleeding & Fading', opt_res.get_json()['complaint_types'])

        # 5. Delete unused complaint type
        del_res = self.client.delete(f'/api/master/complaint-types/{ct_id}')
        self.assertEqual(del_res.status_code, 200)

    def test_complaint_types_deletion_protection(self):
        """Complaint type referenced in existing complaints CANNOT be deleted."""
        self._login_as('superadmin@example.com', 'Super Admin', 'Super Admin One')

        # Lodge a complaint using 'Product Defect'
        db = MockSheetsDB
        db.lodge_complaint(
            customer_name='John Doe',
            department_name='Customer Operations',
            complain_types='Product Defect',
            from_department='Online',
            complain_details='Stitching came off jacket.'
        )

        # Attempt to delete 'Product Defect'
        pt = db.get_complaint_type_by_name('Product Defect')
        self.assertIsNotNone(pt)
        del_res = self.client.delete(f"/api/master/complaint-types/{pt['Type ID']}")
        self.assertEqual(del_res.status_code, 409)
        self.assertIn('Cannot delete Complaint Type', del_res.get_json().get('message', ''))
        self.assertIn('deactivate', del_res.get_json().get('message', ''))

    def test_complaint_types_rbac(self):
        """Regular users are blocked from creating/updating/deleting complaint types."""
        self._login_as('user@example.com', 'User', 'Regular User')
        post_res = self.client.post('/api/master/complaint-types', json={'name': 'Hacked Type'})
        self.assertEqual(post_res.status_code, 403)

    # =========================================================================
    # 3. Salesperson CRUD & Validation
    # =========================================================================

    def test_salespersons_admin_crud(self):
        """Admin can Add, Edit, Activate, Deactivate, and Delete salesperson records."""
        self._login_as('superadmin@example.com', 'Super Admin', 'Super Admin One')

        # 1. Create a new salesperson
        create_res = self.client.post('/api/master/salespersons', json={
            'name': 'Ananya Sen',
            'phone': '+91 98765 43219',
            'email': 'ananya.sen@company.local'
        })
        self.assertEqual(create_res.status_code, 201)
        sp_data = create_res.get_json().get('salesperson')
        sp_id = sp_data['Salesperson ID']
        self.assertEqual(sp_data['Name'], 'Ananya Sen')

        # 2. Duplicate creation prevention
        dup_res = self.client.post('/api/master/salespersons', json={
            'name': 'Ananya Sen',
            'phone': '+91 98765 43219',
            'email': 'ananya.sen@company.local'
        })
        self.assertEqual(dup_res.status_code, 400)
        self.assertIn('already exists', dup_res.get_json().get('message', ''))

        # 3. Update salesperson
        upd_res = self.client.put(f'/api/master/salespersons/{sp_id}', json={
            'name': 'Ananya Sen Gupta',
            'phone': '+91 98765 99999',
            'email': 'ananya.sg@company.local',
            'is_active': 'TRUE'
        })
        self.assertEqual(upd_res.status_code, 200)
        self.assertEqual(upd_res.get_json()['salesperson']['Name'], 'Ananya Sen Gupta')

        # 4. Deactivate salesperson
        deact_res = self.client.put(f'/api/master/salespersons/{sp_id}', json={
            'name': 'Ananya Sen Gupta',
            'is_active': 'FALSE'
        })
        self.assertEqual(deact_res.status_code, 200)

        # Verify inactive salesperson is not returned in active options
        opt_res = self.client.get('/api/master/options')
        self.assertNotIn('Ananya Sen Gupta', opt_res.get_json()['salespersons'])

        # 5. Delete unused salesperson
        del_res = self.client.delete(f'/api/master/salespersons/{sp_id}')
        self.assertEqual(del_res.status_code, 200)

    def test_salespersons_deletion_protection(self):
        """Salesperson referenced in existing complaints CANNOT be deleted."""
        self._login_as('superadmin@example.com', 'Super Admin', 'Super Admin One')

        # Lodge a complaint using 'Vikram Rao'
        db = MockSheetsDB
        db.lodge_complaint(
            customer_name='Jane Smith',
            department_name='Customer Operations',
            complain_types='Billing Discrepancy',
            salesperson_name='Vikram Rao',
            from_department='Offline',
            complain_details='Incorrect tax slab applied.'
        )

        sp = db.get_salesperson_by_name('Vikram Rao')
        self.assertIsNotNone(sp)
        del_res = self.client.delete(f"/api/master/salespersons/{sp['Salesperson ID']}")
        self.assertEqual(del_res.status_code, 409)
        self.assertIn('Cannot delete Salesperson', del_res.get_json().get('message', ''))
        self.assertIn('deactivate', del_res.get_json().get('message', ''))

    # =========================================================================
    # 4. From Department Validation & Persistence
    # =========================================================================

    def test_lodge_complaint_from_department_validation(self):
        """From Department is required and strictly constrained to: Online, Offline, Packing, None."""
        self._login_as('user@example.com', 'User', 'Regular User')

        # Missing From Department defaults to 'None' for backward compatibility
        res1 = self.client.post('/api/complaints', data={
            'customer_name': 'Alice Brown',
            'department_name': 'Customer Operations',
            'complain_types': 'Delivery Delay',
            'complain_details': 'Shipment delayed by 4 days.'
        })
        self.assertEqual(res1.status_code, 201)
        self.assertEqual(res1.get_json()['complaint']['From Department'], 'None')

        # Invalid From Department -> 400
        res2 = self.client.post('/api/complaints', data={
            'customer_name': 'Alice Brown',
            'department_name': 'Customer Operations',
            'complain_types': 'Delivery Delay',
            'from_department': 'Warehouse',
            'complain_details': 'Shipment delayed by 4 days.'
        })
        self.assertEqual(res2.status_code, 400)
        self.assertIn('Invalid From Department', res2.get_json().get('message', ''))

        # Valid options all succeed
        for valid_from_dept in ['Online', 'Offline', 'Packing', 'None']:
            res = self.client.post('/api/complaints', data={
                'customer_name': f'Customer {valid_from_dept}',
                'department_name': 'Customer Operations',
                'complain_types': 'Delivery Delay',
                'from_department': valid_from_dept,
                'complain_details': f'Details for {valid_from_dept}'
            })
            self.assertEqual(res.status_code, 201)
            record = res.get_json().get('complaint')
            self.assertEqual(record.get('From Department'), valid_from_dept)

    def test_update_complaint_from_department(self):
        """Updating complaint properly validates and persists From Department."""
        self._login_as('superadmin@example.com', 'Super Admin', 'Super Admin One')

        # Lodge initial complaint
        db = MockSheetsDB
        c = db.lodge_complaint(
            customer_name='Bob Smith',
            department_name='Customer Operations',
            complain_types='Delivery Delay',
            from_department='Online',
            complain_details='Order tracking stuck.'
        )
        cid = c['Complaint ID']

        # Update to another valid From Department
        upd_res = self.client.put(f'/api/complaints/{cid}', json={
            'from_department': 'Packing'
        })
        self.assertEqual(upd_res.status_code, 200)
        updated = upd_res.get_json().get('complaint')
        self.assertEqual(updated.get('From Department'), 'Packing')

        # Invalid From Department update rejected
        bad_upd = self.client.put(f'/api/complaints/{cid}', json={
            'from_department': 'InvalidDept'
        })
        self.assertEqual(bad_upd.status_code, 400)

if __name__ == '__main__':
    unittest.main()
