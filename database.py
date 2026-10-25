"""SQLite persistence for products and orders. No Google Sheets required."""
import os, sqlite3, json
from contextlib import contextmanager
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "data", "sales_bot.db"))
SEED_PATH = os.path.join(BASE_DIR, "data", "products.json")


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
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
            rows = c.execute("SELECT * FROM products ORDER BY created_at DESC, name").fetchall()
        result = []
        for row in rows:
            p = dict(row)
            p["colors"] = json.loads(p.pop("colors_json") or "[]")
            p["page_ids"] = json.loads(p.pop("page_ids_json") or '["*"]')
            p["active"] = bool(p["active"])
            result.append(p)
        return result

    def find_order_by_idempotency_key(self, key):
        with connect() as c:
            row = c.execute("SELECT * FROM orders WHERE idempotency_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def save_order(self, order):
        with connect() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO orders
                (order_id,created_at,page_id,psid,product_code,product_name,unit_price,quantity,
                 color,customer_name,phone,address,delivery,total,payment_method,status,idempotency_key)
                VALUES (:order_id,:created_at,:page_id,:psid,:product_code,:product_name,:unit_price,
                 :quantity,:color,:customer_name,:phone,:address,:delivery,:total,:payment_method,
                 :status,:idempotency_key)""", order)
            if cur.rowcount == 1:
                c.execute("UPDATE products SET stock=MAX(0, stock-?) WHERE code=?",
                          (int(order["quantity"]), order["product_code"]))


def init_db():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS products (
          code TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT DEFAULT '',
          price INTEGER NOT NULL DEFAULT 0, stock INTEGER NOT NULL DEFAULT 0,
          size TEXT DEFAULT '', mustard_grams TEXT DEFAULT '', cover_material TEXT DEFAULT '',
          colors_json TEXT DEFAULT '[]', image_url TEXT DEFAULT '', description TEXT DEFAULT '',
          page_ids_json TEXT DEFAULT '["*"]', active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS orders (
          order_id TEXT PRIMARY KEY, created_at TEXT, page_id TEXT, psid TEXT, product_code TEXT,
          product_name TEXT, unit_price INTEGER, quantity INTEGER, color TEXT, customer_name TEXT,
          phone TEXT, address TEXT, delivery INTEGER, total INTEGER, payment_method TEXT,
          status TEXT, idempotency_key TEXT UNIQUE)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC)")
        c.execute("""CREATE TABLE IF NOT EXISTS admin_users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff',
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login TEXT DEFAULT '')""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_admin_users_active ON admin_users(active)")
        count = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
        if count == 0 and os.path.exists(SEED_PATH):
            with open(SEED_PATH, encoding="utf-8") as f:
                seeds = json.load(f)
            for p in seeds:
                c.execute("""INSERT OR IGNORE INTO products
                  (code,name,category,price,stock,size,mustard_grams,cover_material,colors_json,
                   image_url,description,page_ids_json,active,created_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    str(p.get("code", "")).upper(), p.get("name", ""), p.get("category", ""),
                    int(p.get("price") or 0), int(p.get("stock") or 0), p.get("size", ""),
                    str(p.get("mustard_grams", "")), p.get("cover_material", ""),
                    json.dumps(p.get("colors", []), ensure_ascii=False), p.get("image_url", ""),
                    p.get("description", ""), json.dumps(p.get("page_ids", ["*"]), ensure_ascii=False),
                    int(bool(p.get("active", True))), datetime.now(timezone.utc).isoformat()))
