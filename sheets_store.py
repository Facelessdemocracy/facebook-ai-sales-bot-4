import os
import threading

class SheetsStore:
    """One spreadsheet configuration per Page; a production DB is recommended for atomic stock/order locking."""
    def __init__(self, page_config):
        self.enabled = os.getenv("GOOGLE_SHEETS_ENABLED", "true").lower() == "true"
        self.spreadsheet_id = page_config.get("spreadsheet_id") or os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
        self.products_tab = page_config.get("products_tab", "Products")
        self.orders_tab = page_config.get("orders_tab", "Orders")
        self._lock = threading.RLock()
        self._service = None
        if self.enabled:
            if not self.spreadsheet_id:
                raise RuntimeError("spreadsheet_id missing for Page")
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
            credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service-account.json")
            credentials = Credentials.from_service_account_file(
                credentials_file,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _values(self, range_name):
        return self._service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=range_name
        ).execute().get("values", [])

    def get_products(self):
        if not self.enabled:
            return []
        rows = self._values(f"{self.products_tab}!A1:M")
        if len(rows) < 2:
            return []
        headers = [str(x).strip() for x in rows[0]]
        result = []
        for row in rows[1:]:
            data = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
            if not data.get("code"):
                continue
            try:
                price = int(float(data.get("price", "0")))
                stock = int(float(data.get("stock", "0")))
            except (TypeError, ValueError):
                price, stock = 0, 0
            colors = [x.strip() for x in str(data.get("colors", "")).split("|") if x.strip()]
            page_ids = [x.strip() for x in str(data.get("page_ids", "*")).split("|") if x.strip()] or ["*"]
            result.append({
                "code": str(data["code"]).strip().upper(),
                "name": str(data.get("name", "")).strip(),
                "category": str(data.get("category", "")).strip(),
                "price": price,
                "stock": stock,
                "size": str(data.get("size", "")).strip(),
                "mustard_grams": str(data.get("mustard_grams", "")).strip(),
                "cover_material": str(data.get("cover_material", "")).strip(),
                "colors": colors,
                "image_url": str(data.get("image_url", "")).strip(),
                "description": str(data.get("description", "")).strip(),
                "page_ids": page_ids,
                "active": str(data.get("active", "TRUE")).upper() not in ("FALSE", "0", "NO")
            })
        return result

    def _ensure_orders_header(self):
        rows = self._values(f"{self.orders_tab}!A1:Q1")
        if not rows:
            headers = [[
                "order_id", "created_at", "page_id", "psid", "product_code", "product_name",
                "unit_price", "quantity", "color", "customer_name", "phone", "address",
                "delivery", "total", "payment_method", "status", "idempotency_key"
            ]]
            self._service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id, range=f"{self.orders_tab}!A1",
                valueInputOption="RAW", body={"values": headers}
            ).execute()

    def find_order_by_idempotency_key(self, key):
        if not self.enabled:
            raise RuntimeError("Google Sheets must be enabled for real orders")
        with self._lock:
            self._ensure_orders_header()
            rows = self._values(f"{self.orders_tab}!A1:Q")
            if not rows:
                return None
            headers = rows[0]
            for row in rows[1:]:
                data = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
                if data.get("idempotency_key") == key:
                    return {"order_id": data.get("order_id", "")}
        return None

    def save_order(self, order):
        if not self.enabled:
            raise RuntimeError("Google Sheets must be enabled for real orders")
        with self._lock:
            if self.find_order_by_idempotency_key(order["idempotency_key"]):
                return
            self._ensure_orders_header()
            keys = [
                "order_id", "created_at", "page_id", "psid", "product_code", "product_name",
                "unit_price", "quantity", "color", "customer_name", "phone", "address",
                "delivery", "total", "payment_method", "status", "idempotency_key"
            ]
            values = [[str(order.get(k, "")) for k in keys]]
            self._service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.orders_tab}!A:Q",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": values}
            ).execute()
