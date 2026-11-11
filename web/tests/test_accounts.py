import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.accounts import AccountStore, COOKIE_NAME
from web.engine import ROOT, read_catalog
from web.server import app


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "accounts.sqlite3"
        self.store = AccountStore(self.path)
        self.rows = read_catalog(ROOT / "data" / "gifs.json")
        for patcher in (
            patch("web.server.AccountStore", return_value=self.store),
            patch("web.server.read_catalog", return_value=self.rows),
            patch("web.server.Retriever", side_effect=ValueError("No index needed for account tests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.addCleanup(lambda: self.context.__exit__(None, None, None))

    def register(self, username="Alice"):
        return self.client.post("/api/auth/register", json={"username": username, "password": "correct horse battery"})

    def login(self, username="Alice"):
        return self.client.post("/api/auth/login", json={"username": username, "password": "correct horse battery"})

    def test_registration_requires_explicit_login(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("set-cookie", response.headers)
        self.assertIsNone(self.client.cookies.get(COOKIE_NAME))
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"])
        self.assertEqual(self.client.get("/api/favorites").status_code, 401)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["username"], "Alice")

    def test_isolation_persistence_and_session_revocation(self):
        gif_id = self.rows[-1]["id"]
        self.assertEqual(self.client.put(f"/api/favorites/{gif_id}").status_code, 401)
        response = self.register()
        self.assertEqual(response.status_code, 201)
        response = self.login()
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        alice_token = self.client.cookies.get(COOKIE_NAME)
        self.assertEqual(self.client.put(f"/api/favorites/{gif_id}").status_code, 200)
        self.client.put(f"/api/favorites/{gif_id}")
        self.assertEqual(self.client.get("/api/favorites").json()["saved"], [gif_id])
        # Reopen the SQLite store as after restarting the application.
        with patch.object(app.state, "accounts", AccountStore(self.path)):
            self.assertEqual(self.client.get("/api/auth/me").json()["saved"], [gif_id])
        self.client.post("/api/auth/logout")
        self.assertIsNone(self.store.user_for(alice_token))
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"])
        self.register("Bob")
        self.login("Bob")
        self.assertEqual(self.client.get("/api/favorites").json()["saved"], [])
        self.client.delete(f"/api/favorites/{gif_id}")
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery"})
        self.assertEqual(self.client.get("/api/auth/me").json()["saved"], [gif_id])
        self.client.delete(f"/api/favorites/{gif_id}")
        self.assertEqual(self.client.get("/api/favorites").json()["saved"], [])

    def test_validation_password_storage_and_private_files(self):
        self.assertEqual(self.register().status_code, 201)
        self.login()
        self.assertEqual(self.register("alice").status_code, 409)
        for body in ({"username": "x", "password": "short"},
                     {"username": "bad name", "password": "long enough password"}):
            self.assertEqual(self.client.post("/api/auth/register", json=body).status_code, 422)
        response = self.client.post("/api/auth/login", json={"username": "Alice", "password": "wrong password"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.put("/api/favorites/missing").status_code, 404)
        self.assertEqual(self.client.get("/.local/accounts.sqlite3").status_code, 404)
        self.assertEqual(self.client.get("/api/auth/me").headers["cache-control"], "no-store")
        with self.store.connect() as db:
            row = db.execute("SELECT password_hash FROM users").fetchone()
            self.assertNotEqual(row[0], "correct horse battery")
            self.assertEqual(len(row[0]), 64)
            token = self.client.cookies.get(COOKIE_NAME)
            self.assertNotEqual(db.execute("SELECT token_hash FROM sessions").fetchone()[0], token)
            db.execute("UPDATE sessions SET expires = ?", (int(time.time()) - 1,))
        self.assertEqual(self.client.get("/api/favorites").status_code, 401)

    def test_cross_origin_writes_blocked_and_local_preview_works(self):
        response = self.client.post("/api/auth/register", headers={"Origin": "https://unrelated.example"},
                                    json={"username": "Alice", "password": "long enough password"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/auth/register", headers={"Origin": "http://127.0.0.1:5500"},
                                    json={"username": "Alice", "password": "long enough password"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        preflight = self.client.options("/api/favorites/example", headers={
            "Origin": "http://127.0.0.1:5500", "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(preflight.status_code, 200)


if __name__ == "__main__":
    unittest.main()
