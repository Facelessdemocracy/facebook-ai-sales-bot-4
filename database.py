"""SQLite persistence for products and orders. No Google Sheets required."""
import os, sqlite3, json
from contextlib import contextmanager
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv('DATABASE_PATH', os.path.join(BASE_DIR, 'data', 'sales_bot.db'))
SEED_PATH = os.path.join(BASE_DIR, 'data', 'products.json')

@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

class SQLiteStore:
    def __init__(self, page_config=None):
        self.page_config = page_config or {}
        init_db()

    def get_products(self):
        with connect() as c:
            rows = c.execute('SELECT * FROM products ORDER BY created_at DESC, name').fetchall()
        result=[]
        for r in rows:
            p=dict(r)
            p['colors']=json.loads(p.pop('colors_json') or '[]')
            p['page_ids']=json.loads(p.pop('page_ids_json') or '["*"]')
            p['active']=bool(p['active'])
            result.append(p)
        return result

    def find_order_by_idempotency_key(self, key):
        with connect() as c:
            row=c.execute('SELECT * FROM orders WHERE idempotency_key=?', (key,)).fetchone()
        return dict(row) if row else None

    def save_order(self, order):
        with connect() as c:
            cur = c.execute('''INSERT OR IGNORE INTO orders
            (order_id,created_at,page_id,psid,product_code,product_name,unit_price,quantity,color,customer_name,phone,address,delivery,total,payment_method,status,idempotency_key)
            VALUES (:order_id,:created_at,:page_id,:psid,:product_code,:product_name,:unit_price,:quantity,:color,:customer_name,:phone,:address,:delivery,:total,:payment_method,:status,:idempotency_key)''', order)
            # Reduce stock only when a genuinely new order row was inserted.
            if cur.rowcount == 1:
                c.execute('UPDATE products SET stock=MAX(0, stock-?) WHERE code=?', (int(order['quantity']), order['product_code']))


def init_db():
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS products (
          code TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT DEFAULT '', price INTEGER NOT NULL DEFAULT 0,
          stock INTEGER NOT NULL DEFAULT 0, size TEXT DEFAULT '', mustard_grams TEXT DEFAULT '', cover_material TEXT DEFAULT '',
          colors_json TEXT DEFAULT '[]', image_url TEXT DEFAULT '', description TEXT DEFAULT '', page_ids_json TEXT DEFAULT '["*"]',
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
          order_id TEXT PRIMARY KEY, created_at TEXT, page_id TEXT, psid TEXT, product_code TEXT, product_name TEXT,
          unit_price INTEGER, quantity INTEGER, color TEXT, customer_name TEXT, phone TEXT, address TEXT,
          delivery INTEGER, total INTEGER, payment_method TEXT, status TEXT, idempotency_key TEXT UNIQUE)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC)')
        c.execute('''CREATE TABLE IF NOT EXISTS admin_users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff',
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
          last_login TEXT DEFAULT '')''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_admin_users_active ON admin_users(active)')
        count=c.execute('SELECT COUNT(*) n FROM products').fetchone()['n']
        if count == 0 and os.path.exists(SEED_PATH):
            with open(SEED_PATH, encoding='utf-8') as f: seeds=json.load(f)
            for p in seeds:
                c.execute('''INSERT OR IGNORE INTO products
                (code,name,category,price,stock,size,mustard_grams,cover_material,colors_json,image_url,description,page_ids_json,active,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                  str(p.get('code','')).upper(),p.get('name',''),p.get('category',''),int(p.get('price') or 0),int(p.get('stock') or 0),
                  p.get('size',''),str(p.get('mustard_grams','')),p.get('cover_material',''),json.dumps(p.get('colors',[]),ensure_ascii=False),
                  p.get('image_url',''),p.get('description',''),json.dumps(p.get('page_ids',['*']),ensure_ascii=False),int(bool(p.get('active',True))),datetime.now(timezone.utc).isoformat()))

def upsert_product(data, old_code=None):
    code=str(data.get('code','')).strip().upper()
    if not code: raise ValueError('Product code is required')
    now=datetime.now(timezone.utc).isoformat()
    with connect() as c:
        if old_code and old_code.upper()!=code:
            c.execute('UPDATE orders SET product_code=? WHERE product_code=?',(code,old_code.upper()))
            c.execute('DELETE FROM products WHERE code=?',(old_code.upper(),))
        c.execute('''INSERT INTO products(code,name,category,price,stock,size,mustard_grams,cover_material,colors_json,image_url,description,page_ids_json,active,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,category=excluded.category,price=excluded.price,stock=excluded.stock,size=excluded.size,mustard_grams=excluded.mustard_grams,cover_material=excluded.cover_material,colors_json=excluded.colors_json,image_url=excluded.image_url,description=excluded.description,page_ids_json=excluded.page_ids_json,active=excluded.active''',
          (code,str(data.get('name','')).strip(),str(data.get('category','')).strip(),int(data.get('price') or 0),int(data.get('stock') or 0),str(data.get('size','')).strip(),str(data.get('mustard_grams','')).strip(),str(data.get('cover_material','')).strip(),json.dumps(data.get('colors',[]),ensure_ascii=False),str(data.get('image_url','')).strip(),str(data.get('description','')).strip(),json.dumps(data.get('page_ids',['*']),ensure_ascii=False),int(bool(data.get('active'))),now))

def delete_product(code):
    with connect() as c: c.execute('DELETE FROM products WHERE code=?',(code.upper(),))

def list_orders(status=None, q=None, limit=500):
    sql='SELECT * FROM orders WHERE 1=1'; args=[]
    if status and status!='All': sql+=' AND status=?'; args.append(status)
    if q:
        sql+=' AND (order_id LIKE ? OR customer_name LIKE ? OR phone LIKE ? OR product_name LIKE ?)'; v=f'%{q}%'; args += [v,v,v,v]
    sql+=' ORDER BY created_at DESC LIMIT ?'; args.append(limit)
    with connect() as c: return [dict(r) for r in c.execute(sql,args).fetchall()]

def update_order_status(order_id, status):
    allowed={'Pending','Confirmed','Processing','Shipped','Delivered','Cancelled'}
    if status not in allowed: raise ValueError('Invalid order status')
    with connect() as c: c.execute('UPDATE orders SET status=? WHERE order_id=?',(status,order_id))

def dashboard_stats():
    with connect() as c:
        p=c.execute('SELECT COUNT(*) total, SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) active, COALESCE(SUM(stock),0) stock FROM products').fetchone()
        o=c.execute("SELECT COUNT(*) total, SUM(CASE WHEN status IN ('Pending','Confirmed') THEN 1 ELSE 0 END) open, SUM(CASE WHEN status='Delivered' THEN total ELSE 0 END) sales FROM orders").fetchone()
    return {'products':p['total'] or 0,'active_products':p['active'] or 0,'stock':p['stock'] or 0,'orders':o['total'] or 0,'open_orders':o['open'] or 0,'delivered_sales':o['sales'] or 0}

def all_orders(): return list_orders(limit=10000)


def list_admin_users():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT id,username,role,active,created_at,last_login FROM admin_users ORDER BY id').fetchall()]

def get_admin_user_by_username(username):
    with connect() as c:
        row=c.execute('SELECT * FROM admin_users WHERE username=? COLLATE NOCASE',(username,)).fetchone()
        return dict(row) if row else None

def get_admin_user(user_id):
    with connect() as c:
        row=c.execute('SELECT id,username,role,active,created_at,last_login FROM admin_users WHERE id=?',(user_id,)).fetchone()
        return dict(row) if row else None

def create_admin_user(username, password_hash, role='staff'):
    username=str(username or '').strip()
    if len(username)<3: raise ValueError('Username কমপক্ষে ৩ অক্ষরের হতে হবে।')
    if role not in {'admin','manager','staff'}: raise ValueError('Role সঠিক নয়।')
    with connect() as c:
        c.execute('INSERT INTO admin_users(username,password_hash,role,active,created_at) VALUES(?,?,?,?,?)',
          (username,password_hash,role,1,datetime.now(timezone.utc).isoformat()))

def update_admin_user(user_id, role=None, active=None, password_hash=None):
    if role is not None and role not in {'admin','manager','staff'}: raise ValueError('Role সঠিক নয়।')
    with connect() as c:
        if role is not None: c.execute('UPDATE admin_users SET role=? WHERE id=?',(role,user_id))
        if active is not None: c.execute('UPDATE admin_users SET active=? WHERE id=?',(int(bool(active)),user_id))
        if password_hash is not None: c.execute('UPDATE admin_users SET password_hash=? WHERE id=?',(password_hash,user_id))

def set_user_last_login(user_id):
    with connect() as c: c.execute('UPDATE admin_users SET last_login=? WHERE id=?',(datetime.now(timezone.utc).isoformat(),user_id))

def count_active_admins():
    with connect() as c: return c.execute("SELECT COUNT(*) n FROM admin_users WHERE role='admin' AND active=1").fetchone()['n']
