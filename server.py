"""Local Electronics booking site with a SQLite-backend JSON API."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from getpass import getpass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = Path.home() / ".joe-electronics" / "store.sqlite3"
SESSION_DAYS = 14
MAX_BODY_BYTES = 64 * 1024
PASSWORD_N = 1 << 14
PASSWORD_R = 8
PASSWORD_P = 1
PASSWORD_LENGTH = 64
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SAFE_STATIC_SUFFIXES = {".html", ".css", ".js", ".jpg", ".jpeg", ".jfif", ".png", ".svg", ".webp", ".ico"}
BOOKING_STATUSES = {"new", "contacted", "confirmed", "completed", "cancelled"}
RATE_LIMIT: dict[tuple[str, str], list[float]] = {}
RATE_LOCK = threading.Lock()

STARTER_PRODUCTS = [
    ("Gaming Laptop", "Computers", "Gaming-ready laptop for study, work, and entertainment.", "laptop1.jpg", 39999, 1, 0),
    ("Ultrabook Pro", "Computers", "Lightweight portable laptop for everyday use.", "laptop1.jpg", 34999, 0, 1),
    ("Business Laptop", "Computers", "Reliable laptop for work and productivity.", "laptop1.jpg", 42999, 1, 0),
    ("Creator Laptop", "Computers", "Laptop for design, photo editing, and creative work.", "laptop1.jpg", 45999, 0, 1),
    ("Office Scanner", "Office", "Fast document scanner for home or office.", "scanners.jpg", 7499, 0, 0),
    ("Apex X10", "Smartphones", "Flagship-style smartphone for everyday use.", "smartphone1.jpg", 17999, 1, 1),
    ("Nova 9", "Smartphones", "Smartphone with a bright display and capable camera.", "smartphone1.jpg", 12999, 0, 1),
    ("Eco 5G", "Smartphones", "Affordable 5G phone with long battery life.", "smartphone1.jpg", 9999, 1, 0),
    ("Studio Headset", "Audio", "Over-ear headset for music, calls, and study.", "headphones.jpg", 2499, 1, 0),
    ("Echo Buds", "Audio", "Compact wireless earbuds for daily listening.", "headphones.jpg", 1499, 0, 1),
    ("Pulse Speaker", "Audio", "Portable speaker for home or travel.", "car stereo.jpg", 1999, 0, 0),
    ("Legend Pad", "Gaming", "Comfortable controller for compatible games.", "game.jpg", 1799, 1, 0),
    ("Turbo Keyboard", "Gaming", "Responsive keyboard for gaming and work.", "game.jpg", 2499, 0, 1),
    ("Vortex Mouse", "Gaming", "Responsive mouse for everyday work and gaming.", "game.jpg", 999, 1, 0),
    ("Pulse Watch", "Wearables", "Smart watch with activity and notification features.", "watch.jpg", 2999, 0, 1),
    ("Move Tracker", "Wearables", "Fitness tracker for daily movement and activity.", "watch.jpg", 1999, 1, 0),
    ("DSLR Camera", "Photography", "Digital camera for photos and creative projects.", "DSLR.jpg", 24999, 0, 1),
    ("Wave Router", "Networking", "Wireless router for home and small office networks.", "router.jpg", 3499, 1, 0),
    ("Smart Bulb", "Smart Home", "Smart bulb for connected home lighting.", "smartbulb.jpg", 899, 0, 1),
    ("LED Ring Light", "Photography", "Ring light for calls, portraits, and content creation.", "ringlight.jpg", 1999, 0, 1),
    ("Reverse Camera", "Car Electronics", "Camera to help improve visibility when reversing.", "reverse camera.jpg", 2999, 1, 0),
    ("Solar Charger", "Power & Energy", "Portable solar charging accessory.", "solar charger.jpg", 2499, 0, 1),
]


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def connect_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db(db_path: Path) -> None:
    with connect_db(db_path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                full_name TEXT NOT NULL,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'admin')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                price_ksh INTEGER NOT NULL CHECK (price_ksh >= 0),
                is_flash_sale INTEGER NOT NULL DEFAULT 0 CHECK (is_flash_sale IN (0, 1)),
                is_new_arrival INTEGER NOT NULL DEFAULT 0 CHECK (is_new_arrival IN (0, 1)),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                product_name TEXT NOT NULL,
                category TEXT NOT NULL,
                image_url TEXT NOT NULL,
                price_each_ksh INTEGER NOT NULL CHECK (price_each_ksh >= 0),
                quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 99),
                customer_name TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                phone TEXT NOT NULL,
                delivery_address TEXT NOT NULL,
                requested_date TEXT,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'confirmed', 'completed', 'cancelled')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS bookings_user_created_idx ON bookings(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS bookings_status_created_idx ON bookings(status, created_at DESC);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash BLOB PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS admin_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                previous_admin_id INTEGER NOT NULL REFERENCES users(id),
                new_admin_id INTEGER NOT NULL REFERENCES users(id),
                transferred_at TEXT NOT NULL
            );
            """
        )
        # Keep a single owner if an older local database had more than one admin.
        admins = db.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id").fetchall()
        if len(admins) > 1:
            keep_id = admins[0]["id"]
            db.execute("UPDATE users SET role = 'customer' WHERE role = 'admin' AND id != ?", (keep_id,))
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_store_admin ON users(role) WHERE role = 'admin'")
        product_count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if product_count == 0:
            now = utc_now().isoformat()
            db.executemany(
                """INSERT INTO products
                (name, category, description, image_url, price_ksh, is_flash_sale, is_new_arrival, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(*product, now) for product in STARTER_PRODUCTS],
            )
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now().isoformat(),))


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=PASSWORD_N, r=PASSWORD_R, p=PASSWORD_P,
        dklen=PASSWORD_LENGTH, maxmem=64 * 1024 * 1024,
    )
    return salt, digest


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def iso_now() -> str:
    return utc_now().isoformat()


def public_user(row: sqlite3.Row) -> dict[str, object]:
    return {"id": row["id"], "email": row["email"], "fullName": row["full_name"], "role": row["role"]}


def create_admin(db_path: Path, email: str) -> None:
    email = email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise SystemExit("Provide a valid admin email address.")
    password = getpass("New admin password (at least 12 characters): ")
    confirm = getpass("Confirm admin password: ")
    if len(password) < 12 or len(password) > 128:
        raise SystemExit("Admin password must be between 12 and 128 characters.")
    if password != confirm:
        raise SystemExit("Passwords did not match.")
    full_name = input("Admin full name: ").strip() or "Store Admin"
    salt, digest = hash_password(password)
    with connect_db(db_path) as db:
        current_admin = db.execute("SELECT email FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if current_admin and current_admin["email"].lower() != email:
            raise SystemExit("An admin already owns the store. Sign in as that admin and transfer ownership in the Admin dashboard.")
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        now = iso_now()
        if existing:
            db.execute(
                "UPDATE users SET full_name = ?, password_salt = ?, password_hash = ?, role = 'admin' WHERE id = ?",
                (full_name, salt, digest, existing["id"]),
            )
            print(f"Promoted existing account {email} to admin.")
        else:
            db.execute(
                "INSERT INTO users (email, full_name, password_salt, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'admin', ?)",
                (email, full_name, salt, digest, now),
            )
            print(f"Created admin account {email}.")


class StoreHandler(BaseHTTPRequestHandler):
    server_version = "JoeElectronics/1.0"
    db_path: Path
    secure_cookie = False

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        super().end_headers()

    def send_json(self, status: HTTPStatus, payload: object, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_empty(self, status: HTTPStatus, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()

    def read_json(self) -> dict[str, object]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send request data as application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid request length.") from error
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON request.") from error
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Request body must be a JSON object.")
        return body

    def guard_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urlsplit(origin)
        expected_host = self.headers.get("Host", "").lower()
        if parsed.netloc.lower() != expected_host or parsed.scheme not in {"http", "https"}:
            raise ApiError(HTTPStatus.FORBIDDEN, "Request origin is not allowed.")

    def rate_limit(self, action: str, max_requests: int = 12, window_seconds: int = 600) -> None:
        ip = self.client_address[0]
        now = time.monotonic()
        key = (action, ip)
        with RATE_LOCK:
            hits = [hit for hit in RATE_LIMIT.get(key, []) if now - hit < window_seconds]
            if len(hits) >= max_requests:
                raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "Too many attempts. Wait a few minutes and try again.")
            hits.append(now)
            RATE_LIMIT[key] = hits

    def current_user(self) -> sqlite3.Row | None:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookies.get("electronics_session")
        if not morsel:
            return None
        digest = token_digest(morsel.value)
        with connect_db(self.db_path) as db:
            row = db.execute(
                """SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (digest, iso_now()),
            ).fetchone()
        return row

    def require_user(self) -> sqlite3.Row:
        user = self.current_user()
        if not user:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Sign in to continue.")
        return user

    def require_admin(self) -> sqlite3.Row:
        user = self.require_user()
        if user["role"] != "admin":
            raise ApiError(HTTPStatus.FORBIDDEN, "This action is restricted to store administrators.")
        return user

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/"):
                self.handle_api_get(path)
            elif path == "/admin.html":
                user = self.current_user()
                if not user:
                    self.send_empty(HTTPStatus.SEE_OTHER, {"Location": "/login.html?next=%2Fadmin.html", "Cache-Control": "no-store"})
                    return
                if user["role"] != "admin":
                    raise ApiError(HTTPStatus.FORBIDDEN, "This page is restricted to the store administrator.")
                self.serve_static(path)
            else:
                self.serve_static(path)
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.Error:
            self.log_error("SQLite error while serving GET: %s", sys.exc_info()[1])
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Database operation failed."})

    def do_POST(self) -> None:
        try:
            self.guard_origin()
            self.handle_api_post(urlsplit(self.path).path)
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.Error:
            self.log_error("SQLite error while serving POST: %s", sys.exc_info()[1])
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Database operation failed."})

    def do_PATCH(self) -> None:
        try:
            self.guard_origin()
            self.handle_api_patch(urlsplit(self.path).path)
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.Error:
            self.log_error("SQLite error while serving PATCH: %s", sys.exc_info()[1])
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Database operation failed."})

    def do_DELETE(self) -> None:
        try:
            self.guard_origin()
            self.handle_api_delete(urlsplit(self.path).path)
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.Error:
            self.log_error("SQLite error while serving DELETE: %s", sys.exc_info()[1])
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Database operation failed."})

    def handle_api_get(self, path: str) -> None:
        if path == "/api/session":
            user = self.current_user()
            self.send_json(HTTPStatus.OK, {"user": public_user(user) if user else None})
            return
        if path == "/api/products":
            query = parse_qs(urlsplit(self.path).query)
            clauses = ["is_active = 1"]
            params: list[object] = []
            if query.get("flash") == ["1"]:
                clauses.append("is_flash_sale = 1")
            if query.get("new") == ["1"]:
                clauses.append("is_new_arrival = 1")
            with connect_db(self.db_path) as db:
                products = db.execute(
                    f"SELECT * FROM products WHERE {' AND '.join(clauses)} ORDER BY category, name", params
                ).fetchall()
            self.send_json(HTTPStatus.OK, {"products": [dict(row) for row in products]})
            return
        if path == "/api/bookings":
            user = self.require_user()
            with connect_db(self.db_path) as db:
                if user["role"] == "admin":
                    rows = db.execute(
                        """SELECT bookings.*, users.email AS account_email FROM bookings
                        JOIN users ON users.id = bookings.user_id ORDER BY bookings.created_at DESC"""
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT * FROM bookings WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
                    ).fetchall()
            self.send_json(HTTPStatus.OK, {"bookings": [dict(row) for row in rows]})
            return
        if path == "/api/admin/products":
            self.require_admin()
            with connect_db(self.db_path) as db:
                rows = db.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
            self.send_json(HTTPStatus.OK, {"products": [dict(row) for row in rows]})
            return
        raise ApiError(HTTPStatus.NOT_FOUND, "API route not found.")

    def handle_api_post(self, path: str) -> None:
        body = self.read_json()
        if path == "/api/auth/signup":
            self.rate_limit("signup")
            email = str(body.get("email", "")).strip().lower()
            full_name = str(body.get("fullName", "")).strip()
            password = str(body.get("password", ""))
            if not EMAIL_RE.fullmatch(email) or len(email) > 254:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Enter a valid email address.")
            if len(full_name) < 2 or len(full_name) > 120:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Enter your full name (2–120 characters).")
            if len(password) < 10 or len(password) > 128:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Password must be between 10 and 128 characters.")
            salt, digest = hash_password(password)
            try:
                with connect_db(self.db_path) as db:
                    cursor = db.execute(
                        """INSERT INTO users (email, full_name, password_salt, password_hash, role, created_at)
                        VALUES (?, ?, ?, ?, 'customer', ?)""",
                        (email, full_name, salt, digest, iso_now()),
                    )
                    user = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
                    headers = self.new_session(db, user["id"])
            except sqlite3.IntegrityError as error:
                if "users.email" in str(error).lower() or "unique" in str(error).lower():
                    raise ApiError(HTTPStatus.CONFLICT, "An account with this email already exists.") from error
                raise
            self.send_json(HTTPStatus.CREATED, {"user": public_user(user)}, headers)
            return

        if path == "/api/auth/login":
            self.rate_limit("login", max_requests=10)
            email = str(body.get("email", "")).strip().lower()
            password = str(body.get("password", ""))
            if not 1 <= len(password) <= 128:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "Email or password is incorrect.")
            with connect_db(self.db_path) as db:
                user = db.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
                if user:
                    _, candidate = hash_password(password, user["password_salt"])
                    valid = hmac.compare_digest(candidate, user["password_hash"])
                else:
                    # Spend comparable password-hash work for unknown accounts.
                    hash_password(password or "dummy-password", b"store-login-dummy")
                    valid = False
                if not valid:
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "Email or password is incorrect.")
                headers = self.new_session(db, user["id"])
            self.send_json(HTTPStatus.OK, {"user": public_user(user)}, headers)
            return

        if path == "/api/auth/logout":
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            token = cookies.get("electronics_session")
            if token:
                with connect_db(self.db_path) as db:
                    db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token.value),))
            self.send_empty(HTTPStatus.NO_CONTENT, {"Set-Cookie": self.clear_cookie()})
            return

        if path == "/api/admin/transfer":
            admin = self.require_admin()
            self.rate_limit("admin-transfer", max_requests=3, window_seconds=3600)
            email = str(body.get("email", "")).strip().lower()
            password = str(body.get("password", ""))
            confirmation = str(body.get("confirmation", ""))
            if not EMAIL_RE.fullmatch(email):
                raise ApiError(HTTPStatus.BAD_REQUEST, "Enter the exact email of the registered account receiving admin ownership.")
            if confirmation != "TRANSFER ADMIN":
                raise ApiError(HTTPStatus.BAD_REQUEST, "Type TRANSFER ADMIN to confirm this change.")
            if not 1 <= len(password) <= 128:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "Enter your current password to confirm ownership transfer.")
            with connect_db(self.db_path) as db:
                current = db.execute("SELECT * FROM users WHERE id = ? AND role = 'admin'", (admin["id"],)).fetchone()
                if not current:
                    raise ApiError(HTTPStatus.FORBIDDEN, "Your admin role is no longer active.")
                _, password_hash = hash_password(password, current["password_salt"])
                if not hmac.compare_digest(password_hash, current["password_hash"]):
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "Current password is incorrect.")
                successor = db.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
                if not successor:
                    raise ApiError(HTTPStatus.NOT_FOUND, "That email does not belong to a registered account yet.")
                if successor["id"] == current["id"]:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a different registered account.")
                db.execute("UPDATE users SET role = 'customer' WHERE id = ?", (current["id"],))
                db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (successor["id"],))
                db.execute(
                    "INSERT INTO admin_transfers (previous_admin_id, new_admin_id, transferred_at) VALUES (?, ?, ?)",
                    (current["id"], successor["id"], iso_now()),
                )
                db.execute("DELETE FROM sessions WHERE user_id = ?", (current["id"],))
            self.send_json(HTTPStatus.OK, {"message": "Admin ownership transferred. Your session has been signed out."}, {"Set-Cookie": self.clear_cookie()})
            return

        if path == "/api/bookings":
            user = self.require_user()
            product_id = body.get("productId")
            try:
                product_id = int(product_id)
                quantity = int(body.get("quantity", 1))
            except (TypeError, ValueError) as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Select a valid product and quantity.") from error
            if not 1 <= quantity <= 99:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Quantity must be from 1 to 99.")
            customer_name = str(body.get("customerName", "")).strip()
            phone = str(body.get("phone", "")).strip()
            address = str(body.get("address", "")).strip()
            notes = str(body.get("notes", "")).strip()
            if not 2 <= len(customer_name) <= 120:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Enter your full name.")
            if not 5 <= len(phone) <= 40:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Enter a valid phone number.")
            if not 5 <= len(address) <= 500:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Enter a delivery address.")
            if len(notes) > 500:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Notes must be 500 characters or fewer.")
            requested_date = str(body.get("requestedDate", "")).strip() or None
            if requested_date:
                try:
                    date.fromisoformat(requested_date)
                except ValueError as error:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a valid requested date.") from error
            with connect_db(self.db_path) as db:
                product = db.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
                if not product:
                    raise ApiError(HTTPStatus.NOT_FOUND, "This product is no longer available.")
                cursor = db.execute(
                    """INSERT INTO bookings
                    (user_id, product_id, product_name, category, image_url, price_each_ksh, quantity,
                     customer_name, customer_email, phone, delivery_address, requested_date, notes, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
                    (user["id"], product["id"], product["name"], product["category"], product["image_url"],
                     product["price_ksh"], quantity, customer_name, user["email"], phone, address,
                     requested_date, notes, iso_now(), iso_now()),
                )
                booking = db.execute("SELECT * FROM bookings WHERE id = ?", (cursor.lastrowid,)).fetchone()
            self.send_json(HTTPStatus.CREATED, {"booking": dict(booking)})
            return

        if path == "/api/admin/products":
            self.require_admin()
            product = self.validate_product(body)
            with connect_db(self.db_path) as db:
                cursor = db.execute(
                    """INSERT INTO products (name, category, description, image_url, price_ksh, is_flash_sale, is_new_arrival, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (*product, iso_now()),
                )
                row = db.execute("SELECT * FROM products WHERE id = ?", (cursor.lastrowid,)).fetchone()
            self.send_json(HTTPStatus.CREATED, {"product": dict(row)})
            return

        raise ApiError(HTTPStatus.NOT_FOUND, "API route not found.")

    def handle_api_patch(self, path: str) -> None:
        self.require_admin()
        match = re.fullmatch(r"/api/admin/bookings/(\d+)", path)
        if not match:
            raise ApiError(HTTPStatus.NOT_FOUND, "API route not found.")
        body = self.read_json()
        status = str(body.get("status", ""))
        if status not in BOOKING_STATUSES:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a valid booking status.")
        with connect_db(self.db_path) as db:
            cursor = db.execute("UPDATE bookings SET status = ?, updated_at = ? WHERE id = ?", (status, iso_now(), int(match.group(1))))
            if cursor.rowcount == 0:
                raise ApiError(HTTPStatus.NOT_FOUND, "Booking not found.")
            row = db.execute("SELECT * FROM bookings WHERE id = ?", (int(match.group(1)),)).fetchone()
        self.send_json(HTTPStatus.OK, {"booking": dict(row)})

    def handle_api_delete(self, path: str) -> None:
        self.require_admin()
        match = re.fullmatch(r"/api/admin/products/(\d+)", path)
        if not match:
            raise ApiError(HTTPStatus.NOT_FOUND, "API route not found.")
        with connect_db(self.db_path) as db:
            cursor = db.execute("DELETE FROM products WHERE id = ?", (int(match.group(1)),))
            if cursor.rowcount == 0:
                raise ApiError(HTTPStatus.NOT_FOUND, "Product not found.")
        self.send_empty(HTTPStatus.NO_CONTENT)

    def validate_product(self, body: dict[str, object]) -> tuple[object, ...]:
        name = str(body.get("name", "")).strip()
        category = str(body.get("category", "")).strip()
        description = str(body.get("description", "")).strip()
        image_url = str(body.get("imageUrl", "")).strip()
        try:
            price = int(body.get("priceKsh", -1))
        except (TypeError, ValueError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Price must be a whole number of KSh.") from error
        if not 2 <= len(name) <= 120 or not 2 <= len(category) <= 80:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Enter a product name and category.")
        if len(description) > 1000 or len(image_url) > 1000:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Description or image URL is too long.")
        if not 0 <= price <= 100_000_000:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Enter a valid product price.")
        if image_url and (image_url.startswith("//") or ":" in image_url.split("/", 1)[0] and not image_url.startswith(("https://", "http://"))):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Image must be a local path or an HTTP(S) URL.")
        return (
            name, category, description, image_url, price,
            int(bool(body.get("isFlashSale"))), int(bool(body.get("isNewArrival"))),
        )

    def new_session(self, db: sqlite3.Connection, user_id: int) -> dict[str, str]:
        token = secrets.token_urlsafe(32)
        expires = utc_now() + timedelta(days=SESSION_DAYS)
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso_now(),))
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_digest(token), user_id, expires.isoformat(), iso_now()),
        )
        cookie = f"electronics_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_DAYS * 86400}"
        if self.secure_cookie:
            cookie += "; Secure"
        return {"Set-Cookie": cookie}

    def clear_cookie(self) -> str:
        cookie = "electronics_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        return cookie + ("; Secure" if self.secure_cookie else "")

    def serve_static(self, request_path: str) -> None:
        decoded = unquote(request_path)
        if decoded == "/":
            decoded = "/index.html"
        candidate = (ROOT / decoded.lstrip("/")).resolve()
        if not candidate.is_relative_to(ROOT) or not candidate.is_file() or candidate.suffix.lower() not in SAFE_STATIC_SUFFIXES:
            raise ApiError(HTTPStatus.NOT_FOUND, "Page or file not found.")
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith(("text/", "application/javascript", "image/svg")) else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if candidate.suffix.lower() in {".html", ".js", ".css"} else "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Joe Electronics local SQLite booking site")
    parser.add_argument("--host", default=os.environ.get("STORE_HOST", "127.0.0.1"), help="Bind host (default: localhost only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("STORE_PORT", "8000")), help="HTTP port")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("STORE_DB", DEFAULT_DB)), help="SQLite database path")
    parser.add_argument("--secure-cookie", action="store_true", help="Set Secure on auth cookies when served through HTTPS")
    parser.add_argument("command", nargs="?", choices=["create-admin"], help="Optional admin setup command")
    parser.add_argument("email", nargs="?", help="Email for create-admin")
    args = parser.parse_args()

    initialize_db(args.db)
    if args.command == "create-admin":
        email = args.email or input("Admin email: ")
        create_admin(args.db, email)
        return

    handler = type("ConfiguredStoreHandler", (StoreHandler,), {"db_path": args.db, "secure_cookie": args.secure_cookie})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Store running at http://{args.host}:{args.port}/")
    print(f"SQLite database: {args.db}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()