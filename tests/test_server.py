import http.cookiejar
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server


class QuietStoreHandler(server.StoreHandler):
    def log_message(self, _format, *_args):
        pass


class StoreApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        server.initialize_db(self.db_path)
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("PRAGMA journal_mode = DELETE")
        finally:
            db.close()
        handler = type("TestHandler", (QuietStoreHandler,), {"db_path": self.db_path, "secure_cookie": False})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = False
        self.httpd.block_on_close = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.customer = self.make_client()
        self.other_customer = self.make_client()
        self.admin = self.make_client()
        self.anonymous = self.make_client()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def make_client(self):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        return {"opener": opener, "jar": jar}

    def request(self, client, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Origin": self.base_url}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with client["opener"].open(request, timeout=4) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                payload = json.loads(raw) if raw and "application/json" in content_type else raw.decode("utf-8", errors="replace") if raw else None
                return response.status, payload
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None

    def signup(self, client, email, full_name):
        status, payload = self.request(client, "POST", "/api/auth/signup", {
            "email": email, "fullName": full_name, "password": "correct horse battery staple"
        })
        self.assertEqual(status, 201, payload)
        return payload["user"]

    def test_customer_bookings_are_private_and_prices_are_server_owned(self):
        customer = self.signup(self.customer, "customer@example.test", "Customer One")
        self.signup(self.other_customer, "other@example.test", "Customer Two")
        _, products = self.request(self.customer, "GET", "/api/products")
        product = products["products"][0]
        status, result = self.request(self.customer, "POST", "/api/bookings", {
            "productId": product["id"], "quantity": 2, "customerName": "Customer One",
            "phone": "+254700000000", "address": "Nairobi, Kenya", "priceKsh": 1,
        })
        self.assertEqual(status, 201, result)
        self.assertEqual(result["booking"]["user_id"], customer["id"])
        self.assertEqual(result["booking"]["price_each_ksh"], product["price_ksh"])
        self.assertEqual(result["booking"]["customer_email"], customer["email"])
        own_status, own = self.request(self.customer, "GET", "/api/bookings")
        other_status, other = self.request(self.other_customer, "GET", "/api/bookings")
        self.assertEqual(own_status, 200)
        self.assertEqual(len(own["bookings"]), 1)
        self.assertEqual(other_status, 200)
        self.assertEqual(other["bookings"], [])

    def test_regular_customer_cannot_use_admin_routes_or_promote_self(self):
        status, customer = self.request(self.customer, "POST", "/api/auth/signup", {
            "email": "customer@example.test", "fullName": "Customer", "password": "correct horse battery staple", "role": "admin"
        })
        self.assertEqual(status, 201)
        self.assertEqual(customer["user"]["role"], "customer")
        status, error = self.request(self.customer, "POST", "/api/admin/products", {
            "name": "Hidden Product", "category": "Audio", "description": "No", "imageUrl": "", "priceKsh": 1
        })
        self.assertEqual(status, 403)
        self.assertIn("administrator", error["error"])

    def test_admin_can_list_all_bookings_manage_inventory_and_status(self):
        self.signup(self.customer, "customer@example.test", "Customer One")
        self.signup(self.admin, "admin@example.test", "Store Admin")
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE users SET role='admin' WHERE email='admin@example.test'")
            db.commit()
        finally:
            db.close()
        _, products = self.request(self.customer, "GET", "/api/products")
        product = products["products"][0]
        _, booking = self.request(self.customer, "POST", "/api/bookings", {
            "productId": product["id"], "quantity": 1, "customerName": "Customer One",
            "phone": "+254700000000", "address": "Nairobi, Kenya",
        })
        status, all_bookings = self.request(self.admin, "GET", "/api/bookings")
        self.assertEqual(status, 200)
        self.assertEqual(len(all_bookings["bookings"]), 1)
        booking_id = booking["booking"]["id"]
        status, updated = self.request(self.admin, "PATCH", f"/api/admin/bookings/{booking_id}", {"status": "contacted"})
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["booking"]["status"], "contacted")
        status, created = self.request(self.admin, "POST", "/api/admin/products", {
            "name": "Admin Product", "category": "Audio", "description": "Created by admin",
            "imageUrl": "headphones.jpg", "priceKsh": 1800, "isFlashSale": True,
        })
        self.assertEqual(status, 201, created)
        product_id = created["product"]["id"]
        status, _ = self.request(self.admin, "DELETE", f"/api/admin/products/{product_id}")
        self.assertEqual(status, 204)

    def test_admin_ownership_transfer_requires_current_password_and_logs_out_former_admin(self):
        self.signup(self.admin, "admin@example.test", "Store Admin")
        self.signup(self.other_customer, "successor@example.test", "New Owner")
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE users SET role='admin' WHERE email='admin@example.test'")
            db.commit()
        finally:
            db.close()

        bad_password_status, bad_password = self.request(self.admin, "POST", "/api/admin/transfer", {
            "email": "successor@example.test", "password": "wrong-password", "confirmation": "TRANSFER ADMIN"
        })
        self.assertEqual(bad_password_status, 401)
        self.assertIn("password", bad_password["error"].lower())

        transfer_status, result = self.request(self.admin, "POST", "/api/admin/transfer", {
            "email": "successor@example.test", "password": "correct horse battery staple", "confirmation": "TRANSFER ADMIN"
        })
        self.assertEqual(transfer_status, 200, result)
        former_session_status, former_session = self.request(self.admin, "GET", "/api/session")
        successor_session_status, successor_session = self.request(self.other_customer, "GET", "/api/session")
        self.assertEqual(former_session_status, 200)
        self.assertIsNone(former_session["user"])
        self.assertEqual(successor_session_status, 200)
        self.assertEqual(successor_session["user"]["role"], "admin")
        db = sqlite3.connect(self.db_path)
        try:
            admins = db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(admins, 1)

    def test_database_rejects_second_admin_role(self):
        self.signup(self.admin, "admin@example.test", "Store Admin")
        self.signup(self.other_customer, "second@example.test", "Second Person")
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE users SET role='admin' WHERE email='admin@example.test'")
            db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE users SET role='admin' WHERE email='second@example.test'")
        finally:
            db.close()

    def test_admin_page_requires_admin_authentication(self):
        self.signup(self.customer, "customer@example.test", "Customer")
        customer_status, customer_response = self.request(self.customer, "GET", "/admin.html")
        self.assertEqual(customer_status, 403)
        self.assertIn("restricted", customer_response["error"].lower())

        self.signup(self.admin, "admin@example.test", "Store Admin")
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE users SET role='admin' WHERE email='admin@example.test'")
            db.commit()
        finally:
            db.close()
        admin_status, admin_response = self.request(self.admin, "GET", "/admin.html")
        self.assertEqual(admin_status, 200)
        self.assertIn("Admin dashboard", admin_response)

        anonymous_status, anonymous_response = self.request(self.anonymous, "GET", "/admin.html")
        self.assertEqual(anonymous_status, 200)  # follows the redirect to the login screen
        self.assertIn("Login Page", anonymous_response)

    def test_unauthenticated_customers_cannot_read_booking_data(self):
        status, result = self.request(self.customer, "GET", "/api/bookings")
        self.assertEqual(status, 401)
        self.assertIn("Sign in", result["error"])

    def test_static_storefront_is_served_but_server_source_is_not(self):
        status, _ = self.request(self.customer, "GET", "/index.html")
        self.assertEqual(status, 200)
        status, _ = self.request(self.customer, "GET", "/server.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
