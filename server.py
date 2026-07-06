import os
import json
import sqlite3
import base64
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g, render_template
import qrcode
from io import BytesIO
import base64 as b64

app = Flask(__name__)

# Database setup
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'inventory.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    cursor = db.cursor()
    
    # Create tables
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',
            full_name TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS outlet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS classification (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            color TEXT DEFAULT '#6b7280',
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS email_template (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            classification_id INTEGER UNIQUE,
            subject TEXT DEFAULT '',
            body TEXT DEFAULT '',
            FOREIGN KEY (classification_id) REFERENCES classification(id)
        );
        CREATE TABLE IF NOT EXISTS supplier (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outlet_id INTEGER NOT NULL,
            classification_id INTEGER NOT NULL,
            supplier_id INTEGER,
            name TEXT NOT NULL,
            unit TEXT DEFAULT 'pcs',
            target_qty REAL NOT NULL DEFAULT 5,
            current_qty REAL NOT NULL DEFAULT 0,
            reorder_point REAL DEFAULT 0,
            cost_per_unit REAL DEFAULT 0,
            supplier_name TEXT DEFAULT '',
            supplier_email TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (outlet_id) REFERENCES outlet(id),
            FOREIGN KEY (classification_id) REFERENCES classification(id)
        );
        CREATE TABLE IF NOT EXISTS stock_check (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            outlet_id INTEGER NOT NULL,
            checked_qty REAL NOT NULL,
            checked_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (item_id) REFERENCES item(id)
        );
        CREATE TABLE IF NOT EXISTS supplier_order (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outlet_id INTEGER NOT NULL,
            status TEXT DEFAULT 'draft',
            checked_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            sent_at TEXT,
            FOREIGN KEY (outlet_id) REFERENCES outlet(id)
        );
        CREATE TABLE IF NOT EXISTS order_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            classification TEXT DEFAULT '',
            qty_needed REAL NOT NULL,
            unit TEXT DEFAULT 'pcs',
            supplier_name TEXT DEFAULT '',
            supplier_email TEXT DEFAULT '',
            FOREIGN KEY (order_id) REFERENCES supplier_order(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES item(id)
        );
        CREATE TABLE IF NOT EXISTS waste_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            outlet_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT DEFAULT 'pcs',
            reason TEXT DEFAULT '',
            cost_loss REAL DEFAULT 0,
            logged_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (item_id) REFERENCES item(id)
        );
        CREATE TABLE IF NOT EXISTS stock_movement (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            outlet_id INTEGER NOT NULL,
            from_qty REAL DEFAULT 0,
            to_qty REAL DEFAULT 0,
            movement_type TEXT DEFAULT 'manual',
            moved_by TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (item_id) REFERENCES item(id)
        );
    ''')
    
    # Seed data if tables are empty
    cursor.execute("SELECT COUNT(*) FROM user")
    if cursor.fetchone()[0] == 0:
        seed_data(db)
    
    db.commit()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def seed_data(db):
    cursor = db.cursor()
    
    # Users
    users = [
        ('admin', hash_password('admin123'), 'admin', 'Admin'),
        ('staff', hash_password('staff123'), 'staff', 'Staff')
    ]
    cursor.executemany(
        'INSERT INTO user (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)',
        users
    )
    
    # Outlets
    outlets = [
        ('Main Kitchen', 'Downtown'),
        ('Branch Kitchen', 'Mall Area'),
        ('Catering Unit', 'Industrial Zone')
    ]
    cursor.executemany(
        'INSERT INTO outlet (name, location) VALUES (?, ?)',
        outlets
    )
    
    # Classifications
    classifications = [
        ('Meat', '#dc2626', 1),
        ('Grocery', '#2563eb', 2),
        ('Vegetable', '#16a34a', 3)
    ]
    cursor.executemany(
        'INSERT INTO classification (name, color, sort_order) VALUES (?, ?, ?)',
        classifications
    )
    
    # Email templates
    templates = [
        (1, 'Order Request - Meat Items', 'Dear Supplier,\n\n{items}\n\nTotal Items: {count}\n\nDelivery to: {outlet}\n\nThank you.'),
        (2, 'Order Request - Grocery Items', 'Dear Supplier,\n\n{items}\n\nTotal Items: {count}\n\nDelivery to: {outlet}\n\nThank you.'),
        (3, 'Order Request - Vegetable Items', 'Dear Supplier,\n\n{items}\n\nTotal Items: {count}\n\nDelivery to: {outlet}\n\nThank you.')
    ]
    cursor.executemany(
        'INSERT INTO email_template (classification_id, subject, body) VALUES (?, ?, ?)',
        templates
    )
    
    # Suppliers
    suppliers = [
        ('Meat Suppliers Co', 'orders@meatsuppliers.com', '+60-1234-5678'),
        ('Ocean Catch', 'orders@oceancatch.com', '+60-2345-6789'),
        ('Farm Fresh Eggs', 'orders@farmfresh.com', '+60-3456-7890'),
        ('Rice Traders', 'orders@ricetraders.com', '+60-4567-8901'),
        ('Dry Goods Supply', 'orders@drygoods.com', '+60-5678-9012'),
        ('Oil Distributors', 'supply@oildist.com', '+60-6789-0123'),
        ('Condiment World', 'orders@condimentworld.com', '+60-7890-1234'),
        ('Dairy Direct', 'supply@dairydirect.com', '+60-8901-2345'),
        ('Green Farm Supply', 'orders@greenfarm.com', '+60-9012-3456')
    ]
    cursor.executemany(
        'INSERT INTO supplier (name, email, phone) VALUES (?, ?, ?)',
        suppliers
    )
    
    db.commit()

def encode_cookie(data):
    json_str = json.dumps(data)
    return base64.urlsafe_b64encode(json_str.encode()).decode()

def decode_cookie(cookie):
    try:
        json_str = base64.urlsafe_b64decode(cookie.encode()).decode()
        return json.loads(json_str)
    except:
        return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_token = request.cookies.get('auth_token')
        if not auth_token:
            return jsonify({'error': 'Authentication required'}), 401
        
        user_data = decode_cookie(auth_token)
        if not user_data or 'exp' not in user_data or datetime.now().timestamp() > user_data['exp']:
            return jsonify({'error': 'Session expired'}), 401
        
        g.user = user_data
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'SELECT id, username, role, full_name, password_hash FROM user WHERE username = ? AND is_active = 1',
        (username,)
    )
    user = cursor.fetchone()
    
    if not user or user['password_hash'] != hash_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    # Set cookie
    exp = (datetime.now() + timedelta(days=1)).timestamp()
    cookie_data = {
        'userId': user['id'],
        'username': user['username'],
        'role': user['role'],
        'fullName': user['full_name'],
        'exp': exp
    }
    
    response = jsonify({
        'success': True,
        'role': user['role'],
        'full_name': user['full_name']
    })
    response.set_cookie('auth_token', encode_cookie(cookie_data), httponly=True, max_age=86400)
    return response

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    response = jsonify({'success': True})
    response.set_cookie('auth_token', '', expires=0)
    return response

@app.route('/api/auth/me', methods=['GET'])
@login_required
def me():
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'SELECT id, username, role, full_name FROM user WHERE id = ?',
        (g.user['userId'],)
    )
    user = cursor.fetchone()
    if not user:
        return jsonify({'error': 'User not found'}), 401
    return jsonify(dict(user))

@app.route('/api/auth/password', methods=['PUT'])
@login_required
def change_password():
    data = request.get_json()
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    
    if not current_password or not new_password:
        return jsonify({'error': 'Current and new password required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'SELECT password_hash FROM user WHERE id = ?',
        (g.user['userId'],)
    )
    user = cursor.fetchone()
    
    if user['password_hash'] != hash_password(current_password):
        return jsonify({'error': 'Current password is incorrect'}), 401
    
    cursor.execute(
        'UPDATE user SET password_hash = ? WHERE id = ?',
        (hash_password(new_password), g.user['userId'])
    )
    db.commit()
    return jsonify({'success': True})

@app.route('/api/outlets', methods=['GET'])
@login_required
def get_outlets():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, name, location, is_active FROM outlet')
    outlets = [dict(row) for row in cursor.fetchall()]
    return jsonify(outlets)

@app.route('/api/outlets', methods=['POST'])
@login_required
@admin_required
def create_outlet():
    data = request.get_json()
    name = data.get('name')
    location = data.get('location', '')
    
    if not name:
        return jsonify({'error': 'Name required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'INSERT INTO outlet (name, location) VALUES (?, ?)',
        (name, location)
    )
    db.commit()
    return jsonify({'success': True, 'id': cursor.lastrowid})

@app.route('/api/outlets/<int:outlet_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_outlet(outlet_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('UPDATE outlet SET is_active = 0 WHERE id = ?', (outlet_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/classifications', methods=['GET'])
@login_required
def get_classifications():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, name, color, sort_order FROM classification ORDER BY sort_order')
    classifications = [dict(row) for row in cursor.fetchall()]
    return jsonify(classifications)

@app.route('/api/suppliers', methods=['GET'])
@login_required
def get_suppliers():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, name, email, phone, is_active FROM supplier')
    suppliers = [dict(row) for row in cursor.fetchall()]
    return jsonify(suppliers)

@app.route('/api/suppliers', methods=['POST'])
@login_required
@admin_required
def create_supplier():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email', '')
    phone = data.get('phone', '')
    
    if not name:
        return jsonify({'error': 'Name required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'INSERT INTO supplier (name, email, phone) VALUES (?, ?, ?)',
        (name, email, phone)
    )
    db.commit()
    return jsonify({'success': True, 'id': cursor.lastrowid})

@app.route('/api/suppliers/<int:supplier_id>', methods=['PUT'])
@login_required
@admin_required
def update_supplier(supplier_id):
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    phone = data.get('phone')
    
    db = get_db()
    cursor = db.cursor()
    updates = []
    params = []
    
    if name is not None:
        updates.append('name = ?')
        params.append(name)
    if email is not None:
        updates.append('email = ?')
        params.append(email)
    if phone is not None:
        updates.append('phone = ?')
        params.append(phone)
    
    if not updates:
        return jsonify({'error': 'No fields to update'}), 400
    
    params.append(supplier_id)
    cursor.execute(
        f'UPDATE supplier SET {", ".join(updates)} WHERE id = ?',
        params
    )
    db.commit()
    return jsonify({'success': True})

@app.route('/api/suppliers/<int:supplier_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_supplier(supplier_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('UPDATE supplier SET is_active = 0 WHERE id = ?', (supplier_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/users', methods=['GET'])
@login_required
@admin_required
def get_users():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, username, role, full_name, is_active FROM user')
    users = [dict(row) for row in cursor.fetchall()]
    return jsonify(users)

@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def create_user():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'staff')
    full_name = data.get('full_name', '')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            'INSERT INTO user (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)',
            (username, hash_password(password), role, full_name)
        )
        db.commit()
        return jsonify({'success': True, 'id': cursor.lastrowid})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Username already exists'}), 400

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
@admin_required
def update_user(user_id):
    data = request.get_json()
    username = data.get('username')
    role = data.get('role')
    full_name = data.get('full_name')
    password = data.get('password')
    is_active = data.get('is_active')
    
    db = get_db()
    cursor = db.cursor()
    updates = []
    params = []
    
    if username is not None:
        updates.append('username = ?')
        params.append(username)
    if role is not None:
        updates.append('role = ?')
        params.append(role)
    if full_name is not None:
        updates.append('full_name = ?')
        params.append(full_name)
    if password is not None:
        updates.append('password_hash = ?')
        params.append(hash_password(password))
    if is_active is not None:
        updates.append('is_active = ?')
        params.append(is_active)
    
    if not updates:
        return jsonify({'error': 'No fields to update'}), 400
    
    params.append(user_id)
    cursor.execute(
        f'UPDATE user SET {", ".join(updates)} WHERE id = ?',
        params
    )
    db.commit()
    return jsonify({'success': True})

@app.route('/api/inventory', methods=['GET'])
@login_required
def get_inventory():
    outlet_id = request.args.get('outlet_id')
    classification_id = request.args.get('classification_id')
    
    db = get_db()
    cursor = db.cursor()
    
    query = '''
        SELECT i.*, c.name as classification_name, c.color as classification_color,
               s.name as supplier_name, s.email as supplier_email
        FROM item i
        JOIN classification c ON i.classification_id = c.id
        LEFT JOIN supplier s ON i.supplier_id = s.id
        WHERE i.is_active = 1
    '''
    params = []
    
    if outlet_id:
        query += ' AND i.outlet_id = ?'
        params.append(outlet_id)
    if classification_id:
        query += ' AND i.classification_id = ?'
        params.append(classification_id)
    
    cursor.execute(query, params)
    items = [dict(row) for row in cursor.fetchall()]
    return jsonify(items)

@app.route('/api/inventory', methods=['POST'])
@login_required
@admin_required
def create_item():
    data = request.get_json()
    outlet_id = data.get('outlet_id')
    classification_id = data.get('classification_id')
    name = data.get('name')
    unit = data.get('unit', 'pcs')
    target_qty = data.get('target_qty', 5)
    current_qty = data.get('current_qty', 0)
    cost_per_unit = data.get('cost_per_unit', 0)
    supplier_name = data.get('supplier_name', '')
    supplier_email = data.get('supplier_email', '')
    
    if not outlet_id or not classification_id or not name:
        return jsonify({'error': 'Outlet, classification, and name required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        '''INSERT INTO item (outlet_id, classification_id, name, unit, target_qty, current_qty, 
                           cost_per_unit, supplier_name, supplier_email)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (outlet_id, classification_id, name, unit, target_qty, current_qty, 
         cost_per_unit, supplier_name, supplier_email)
    )
    db.commit()
    return jsonify({'success': True, 'id': cursor.lastrowid})

@app.route('/api/inventory/<int:item_id>', methods=['PUT'])
@login_required
@admin_required
def update_item(item_id):
    data = request.get_json()
    
    db = get_db()
    cursor = db.cursor()
    
    fields = [
        'outlet_id', 'classification_id', 'name', 'unit', 'target_qty',
        'current_qty', 'cost_per_unit', 'supplier_name', 'supplier_email'
    ]
    
    updates = []
    params = []
    for field in fields:
        if field in data:
            updates.append(f'{field} = ?')
            params.append(data[field])
    
    if not updates:
        return jsonify({'error': 'No fields to update'}), 400
    
    updates.append('updated_at = datetime("now")')
    params.append(item_id)
    
    cursor.execute(
        f'UPDATE item SET {", ".join(updates)} WHERE id = ?',
        params
    )
    db.commit()
    return jsonify({'success': True})

@app.route('/api/inventory/<int:item_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_item(item_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('UPDATE item SET is_active = 0 WHERE id = ?', (item_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/stock-check', methods=['POST'])
@login_required
def stock_check():
    data = request.get_json()
    checks = data.get('checks', [])
    outlet_id = data.get('outlet_id')
    
    if not checks or not outlet_id:
        return jsonify({'error': 'Checks and outlet_id required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    
    for check in checks:
        item_id = check.get('item_id')
        checked_qty = check.get('checked_qty')
        
        if not item_id or checked_qty is None:
            continue
        
        # Get current quantity for movement log
        cursor.execute('SELECT current_qty FROM item WHERE id = ?', (item_id,))
        item = cursor.fetchone()
        if not item:
            continue
        
        old_qty = item['current_qty']
        
        # Insert stock check
        cursor.execute(
            'INSERT INTO stock_check (item_id, outlet_id, checked_qty, checked_by) VALUES (?, ?, ?, ?)',
            (item_id, outlet_id, checked_qty, g.user.get('fullName', ''))
        )
        
        # Update item quantity
        cursor.execute(
            'UPDATE item SET current_qty = ?, updated_at = datetime("now") WHERE id = ?',
            (checked_qty, item_id)
        )
        
        # Log movement
        cursor.execute(
            '''INSERT INTO stock_movement (item_id, outlet_id, from_qty, to_qty, movement_type, moved_by, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (item_id, outlet_id, old_qty, checked_qty, 'stock_check', 
             g.user.get('fullName', ''), f'Stock check from {old_qty} to {checked_qty}')
        )
    
    db.commit()
    return jsonify({'success': True})

@app.route('/api/waste', methods=['POST'])
@login_required
def create_waste():
    data = request.get_json()
    item_id = data.get('item_id')
    outlet_id = data.get('outlet_id')
    quantity = data.get('quantity')
    unit = data.get('unit', 'pcs')
    reason = data.get('reason', '')
    cost_loss = data.get('cost_loss', 0)
    logged_by = data.get('logged_by', g.user.get('fullName', ''))
    
    if not item_id or not outlet_id or quantity is None:
        return jsonify({'error': 'Item, outlet, and quantity required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    
    # Get current quantity
    cursor.execute('SELECT current_qty FROM item WHERE id = ?', (item_id,))
    item = cursor.fetchone()
    if not item:
        return jsonify({'error': 'Item not found'}), 404
    
    old_qty = item['current_qty']
    new_qty = max(0, old_qty - quantity)
    
    # Insert waste log
    cursor.execute(
        '''INSERT INTO waste_log (item_id, outlet_id, quantity, unit, reason, cost_loss, logged_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (item_id, outlet_id, quantity, unit, reason, cost_loss, logged_by)
    )
    
    # Update item quantity
    cursor.execute(
        'UPDATE item SET current_qty = ?, updated_at = datetime("now") WHERE id = ?',
        (new_qty, item_id)
    )
    
    # Log movement
    cursor.execute(
        '''INSERT INTO stock_movement (item_id, outlet_id, from_qty, to_qty, movement_type, moved_by, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (item_id, outlet_id, old_qty, new_qty, 'waste', 
         g.user.get('fullName', ''), f'Waste: {reason}')
    )
    
    db.commit()
    return jsonify({'success': True})

@app.route('/api/waste', methods=['GET'])
@login_required
def get_waste():
    outlet_id = request.args.get('outlet_id')
    
    db = get_db()
    cursor = db.cursor()
    
    query = '''
        SELECT w.*, i.name as item_name
        FROM waste_log w
        JOIN item i ON w.item_id = i.id
        WHERE 1=1
    '''
    params = []
    
    if outlet_id:
        query += ' AND w.outlet_id = ?'
        params.append(outlet_id)
    
    query += ' ORDER BY w.created_at DESC'
    
    cursor.execute(query, params)
    waste = [dict(row) for row in cursor.fetchall()]
    return jsonify(waste)

@app.route('/api/orders', methods=['GET'])
@login_required
def get_orders():
    outlet_id = request.args.get('outlet_id')
    
    db = get_db()
    cursor = db.cursor()
    
    query = '''
        SELECT so.*, 
               (SELECT json_group_array(
                   json_object(
                       'id', oi.id,
                       'item_id', oi.item_id,
                       'item_name', oi.item_name,
                       'classification', oi.classification,
                       'qty_needed', oi.qty_needed,
                       'unit', oi.unit,
                       'supplier_name', oi.supplier_name,
                       'supplier_email', oi.supplier_email
                   )
               ) FROM order_item oi WHERE oi.order_id = so.id) as items
        FROM supplier_order so
        WHERE 1=1
    '''
    params = []
    
    if outlet_id:
        query += ' AND so.outlet_id = ?'
        params.append(outlet_id)
    
    query += ' ORDER BY so.created_at DESC'
    
    cursor.execute(query, params)
    orders = []
    for row in cursor.fetchall():
        order = dict(row)
        if order['items']:
            order['items'] = json.loads(order['items'])
        else:
            order['items'] = []
        orders.append(order)
    
    return jsonify(orders)

@app.route('/api/orders', methods=['POST'])
@login_required
def create_order():
    data = request.get_json()
    item_ids = data.get('item_ids', [])
    outlet_id = data.get('outlet_id')
    
    if not item_ids or not outlet_id:
        return jsonify({'error': 'Item IDs and outlet_id required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    
    # Create order
    cursor.execute(
        'INSERT INTO supplier_order (outlet_id, checked_by) VALUES (?, ?)',
        (outlet_id, g.user.get('fullName', ''))
    )
    order_id = cursor.lastrowid
    
    # Get outlet name
    cursor.execute('SELECT name FROM outlet WHERE id = ?', (outlet_id,))
    outlet = cursor.fetchone()
    outlet_name = outlet['name'] if outlet else ''
    
    # Process each item
    for item_id in item_ids:
        cursor.execute(
            '''SELECT i.*, c.name as classification_name 
               FROM item i
               JOIN classification c ON i.classification_id = c.id
               WHERE i.id = ? AND i.is_active = 1''',
            (item_id,)
        )
        item = cursor.fetchone()
        if not item:
            continue
        
        qty_needed = max(1, item['target_qty'] - item['current_qty'])
        
        cursor.execute(
            '''INSERT INTO order_item 
               (order_id, item_id, item_name, classification, qty_needed, unit, supplier_name, supplier_email)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (order_id, item_id, item['name'], item['classification_name'], 
             qty_needed, item['unit'], item['supplier_name'], item['supplier_email'])
        )
    
    db.commit()
    return jsonify({'success': True, 'orderId': order_id})

@app.route('/api/orders/<int:order_id>', methods=['PUT'])
@login_required
@admin_required
def update_order(order_id):
    data = request.get_json()
    status = data.get('status')
    
    if status == 'sent':
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            'UPDATE supplier_order SET status = ?, sent_at = datetime("now") WHERE id = ?',
            (status, order_id)
        )
        db.commit()
    elif status:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            'UPDATE supplier_order SET status = ? WHERE id = ?',
            (status, order_id)
        )
        db.commit()
    
    return jsonify({'success': True})

@app.route('/api/orders/<int:order_id>/email', methods=['GET'])
@login_required
@admin_required
def get_order_emails(order_id):
    db = get_db()
    cursor = db.cursor()
    
    # Get order details
    cursor.execute(
        '''SELECT so.*, o.name as outlet_name 
           FROM supplier_order so
           JOIN outlet o ON so.outlet_id = o.id
           WHERE so.id = ?''',
        (order_id,)
    )
    order = cursor.fetchone()
    if not order:
        return jsonify({'error': 'Order not found'}), 404
    
    # Get order items grouped by classification
    cursor.execute(
        '''SELECT oi.*, et.subject, et.body
           FROM order_item oi
           LEFT JOIN email_template et ON et.classification_id = (
               SELECT classification_id FROM item WHERE id = oi.item_id
           )
           WHERE oi.order_id = ?''',
        (order_id,)
    )
    items = cursor.fetchall()
    
    # Group by classification
    grouped = {}
    for item in items:
        classification = item['classification']
        if classification not in grouped:
            grouped[classification] = {
                'items': [],
                'subject': item['subject'] or f'Order Request - {classification} Items',
                'body': item['body'] or 'Dear Supplier,\n\n{items}\n\nTotal Items: {count}\n\nDelivery to: {outlet}\n\nThank you.'
            }
        
        grouped[classification]['items'].append({
            'name': item['item_name'],
            'qty': item['qty_needed'],
            'unit': item['unit'],
            'supplier_email': item['supplier_email']
        })
    
    # Generate emails
    emails = []
    for classification, data in grouped.items():
        item_lines = '\n'.join([
            f'- {item["name"]}: {item["qty"]} {item["unit"]}'
            for item in data['items']
        ])
        
        body = data['body'].replace('{items}', item_lines)
        body = body.replace('{count}', str(len(data['items'])))
        body = body.replace('{outlet}', order['outlet_name'])
        
        supplier_emails = set()
        for item in data['items']:
            if item['supplier_email']:
                supplier_emails.add(item['supplier_email'])
        
        emails.append({
            'classification': classification,
            'subject': data['subject'],
            'body': body,
            'itemCount': len(data['items']),
            'supplierEmails': list(supplier_emails),
            'mailtoLink': f"mailto:{','.join(supplier_emails)}?subject={data['subject']}&body={body}"
        })
    
    return jsonify({
        'orderId': order_id,
        'emails': emails
    })

@app.route('/api/qr/<int:outlet_id>', methods=['GET'])
@login_required
def get_qr_code(outlet_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT name FROM outlet WHERE id = ?', (outlet_id,))
    outlet = cursor.fetchone()
    
    if not outlet:
        return jsonify({'error': 'Outlet not found'}), 404
    
    # Generate QR code URL
    base_url = request.host_url.rstrip('/')
    qr_url = f"{base_url}/api/inventory?outlet_id={outlet_id}"
    
    # Use qrcode library without PIL dependency
    import io
    import qrcode as qr_module
    
    qr = qr_module.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_url)
    qr.make(fit=True)
    
    # Create image using the qrcode's built-in method
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save to BytesIO
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG')
    img_buffer.seek(0)
    
    import base64
    img_b64 = base64.b64encode(img_buffer.getvalue()).decode()
    
    return jsonify({
        'qrCode': f"data:image/png;base64,{img_b64}",
        'url': qr_url
    })


@app.route('/api/seed', methods=['POST'])
@login_required
@admin_required
def seed_items():
    data = request.get_json()
    outlet_id = data.get('outlet_id')
    
    if not outlet_id:
        return jsonify({'error': 'outlet_id required'}), 400
    
    db = get_db()
    cursor = db.cursor()
    
    # Check if outlet exists
    cursor.execute('SELECT id FROM outlet WHERE id = ?', (outlet_id,))
    if not cursor.fetchone():
        return jsonify({'error': 'Outlet not found'}), 404
    
    # Seed 20 items
    items = [
        # Meat (classification_id=1)
        (outlet_id, 1, 1, 'Chicken Breast', 'kg', 10, 3),
        (outlet_id, 1, 1, 'Chicken Curry Cut', 'kg', 15, 5),
        (outlet_id, 1, 1, 'Mutton', 'kg', 8, 2),
        (outlet_id, 1, 2, 'Fish Fillet', 'kg', 10, 3),
        (outlet_id, 1, 2, 'Prawns', 'kg', 8, 2),
        (outlet_id, 1, 3, 'Eggs', 'packets', 20, 8),
        (outlet_id, 1, 1, 'Minced Meat', 'kg', 5, 1),
        # Grocery (classification_id=2)
        (outlet_id, 2, 4, 'Rice', 'kg', 10, 7),
        (outlet_id, 2, 5, 'Sugar', 'kg', 5, 4),
        (outlet_id, 2, 5, 'Salt', 'kg', 5, 3),
        (outlet_id, 2, 6, 'Cooking Oil', 'litres', 10, 4),
        (outlet_id, 2, 7, 'Chili Sauce', 'packets', 8, 5),
        (outlet_id, 2, 7, 'Soy Sauce', 'packets', 6, 3),
        (outlet_id, 2, 8, 'Milk', 'litres', 15, 8),
        # Vegetable (classification_id=3)
        (outlet_id, 3, 9, 'Onions', 'kg', 15, 7),
        (outlet_id, 3, 9, 'Tomatoes', 'kg', 10, 4),
        (outlet_id, 3, 9, 'Potatoes', 'kg', 12, 6),
        (outlet_id, 3, 9, 'Garlic', 'kg', 5, 1),
        (outlet_id, 3, 9, 'Ginger', 'kg', 5, 1),
        (outlet_id, 3, 9, 'Mixed Vegetables', 'kg', 10, 3)
    ]
    
    for item in items:
        cursor.execute(
            '''INSERT INTO item (outlet_id, classification_id, supplier_id, name, unit, target_qty, current_qty)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            item
        )
    
    db.commit()
    return jsonify({'success': True, 'count': len(items)})

@app.route('/api/email-templates', methods=['GET'])
@login_required
@admin_required
def get_email_templates():
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        '''SELECT et.*, c.name as classification_name 
           FROM email_template et
           JOIN classification c ON et.classification_id = c.id'''
    )
    templates = [dict(row) for row in cursor.fetchall()]
    return jsonify(templates)

@app.route('/api/email-templates/<int:template_id>', methods=['PUT'])
@login_required
@admin_required
def update_email_template(template_id):
    data = request.get_json()
    subject = data.get('subject')
    body = data.get('body')
    
    db = get_db()
    cursor = db.cursor()
    
    updates = []
    params = []
    
    if subject is not None:
        updates.append('subject = ?')
        params.append(subject)
    if body is not None:
        updates.append('body = ?')
        params.append(body)
    
    if not updates:
        return jsonify({'error': 'No fields to update'}), 400
    
    params.append(template_id)
    cursor.execute(
        f'UPDATE email_template SET {", ".join(updates)} WHERE id = ?',
        params
    )
    db.commit()
    return jsonify({'success': True})

if __name__ == '__main__':
    with app.app_context():
        init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
