"""Local accounts, opaque sessions and per-user favorites in SQLite."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .engine import ROOT

SESSION_SECONDS = 30 * 24 * 60 * 60
COOKIE_NAME = "gifly_session"


class AccountStore:
    def __init__(self, path: Path = ROOT / ".local" / "accounts.sqlite3"):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE, salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS favorites (
                    user_id INTEGER NOT NULL REFERENCES users(id), gif_id TEXT NOT NULL,
                    created INTEGER NOT NULL, PRIMARY KEY (user_id, gif_id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()

    def register(self, username, password):
        salt = secrets.token_hex(16)
        digest = self.password_hash(password, salt)
        try:
            with self.connect() as db:
                cursor = db.execute("INSERT INTO users(username, username_key, salt, password_hash) VALUES (?, ?, ?, ?)",
                                    (username, username.lower(), salt, digest))
                return {"id": cursor.lastrowid, "username": username}
        except sqlite3.IntegrityError:
            raise ValueError("This username is already taken.") from None

    def login(self, username, password):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username_key = ?", (username.lower(),)).fetchone()
        # Perform the same expensive hash even when the account does not exist.
        digest = self.password_hash(password, row["salt"] if row else "00" * 16)
        if row is None or not hmac.compare_digest(digest, row["password_hash"]):
            return None
        return {"id": row["id"], "username": row["username"]}

    def new_session(self, user_id, previous=None):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (int(time.time()),))
            if previous:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(previous),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (self.token_hash(token), user_id, int(time.time()) + SESSION_SECONDS))
        return token

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def user_for(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute("""SELECT u.id, u.username FROM users u JOIN sessions s ON u.id = s.user_id
                                WHERE s.token_hash = ? AND s.expires > ?""",
                             (self.token_hash(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(token),))

    def saved(self, user_id):
        with self.connect() as db:
            return [row[0] for row in db.execute(
                "SELECT gif_id FROM favorites WHERE user_id = ? ORDER BY created, gif_id", (user_id,))]

    def save(self, user_id, gif_id, saved):
        with self.connect() as db:
            if saved:
                db.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?, ?)", (user_id, gif_id, time.time_ns()))
            else:
                db.execute("DELETE FROM favorites WHERE user_id = ? AND gif_id = ?", (user_id, gif_id))
