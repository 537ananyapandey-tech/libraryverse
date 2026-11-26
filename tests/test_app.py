"""Run with:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from app import create_app  # noqa: E402
from data.seed_books import BOOKS, ISSUES  # noqa: E402
from database import connect  # noqa: E402
from services import library  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "test.db")
        self.app = create_app({"DATABASE": self.path, "TESTING": True})
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["csrf_token"] = "tok"
        self.h = {"X-CSRF-Token": "tok"}

    def tearDown(self):
        self.tmp.cleanup()

    def db(self):
        return connect(self.path)

    def book(self, title):
        return self.db().execute("SELECT * FROM books WHERE title = ?", (title,)).fetchone()

    def issue(self, no="CS24002", name="Ananya Verma", book_id=None, **extra):
        with self.client.session_transaction() as s:
            logged_in = bool(s.get("admin_id"))
        if not logged_in:
            self.admin_login()          # direct issuing is an admin-desk action now
        return self.client.post("/api/issue", headers=self.h,
                                json={"student_no": no, "student_name": name, "book_id": book_id, **extra})

    _counter = [0]

    def fresh(self, copies=3, history=False):
        """Insert an untouched test book so tests do not depend on the random demo history."""
        Base._counter[0] += 1
        title = f"Zz Test Book {Base._counter[0]}"
        with self.db() as c:
            cur = c.execute("INSERT INTO books(title, author, category_id, total_copies, available_copies) "
                            "VALUES (?, 'T. Author', 1, ?, ?)", (title, copies, copies))
            if history:
                c.execute("INSERT INTO students(enrollment_no, name, password_hash) VALUES (?, 'Hist Student', 'x')",
                          (f"H{Base._counter[0]:04d}",))
                c.execute("INSERT INTO issue_records(book_id, student_id, issue_date, due_date, returned_at) "
                          "VALUES (?, (SELECT MAX(id) FROM students), '2026-01-01', '2026-01-15', '2026-01-10')",
                          (cur.lastrowid,))
        return self.book(title)

    def admin_login(self):
        r = self.client.post("/admin/login", data={"username": config.DEMO_ADMIN_USERNAME,
                                                  "password": config.DEMO_ADMIN_PASSWORD, "csrf_token": "tok"})
        with self.client.session_transaction() as s:      # login rotates the session; a browser re-reads the new token
            s["csrf_token"] = "tok"
        return r


class SeedData(Base):
    def test_seed_size_and_isbn_checksums(self):
        self.assertGreaterEqual(len(BOOKS), 60)
        for row in BOOKS:
            if row[4]:
                self.assertTrue(library.is_valid_isbn(row[4]), f"Bad ISBN checksum for {row[0]}: {row[4]}")
        titles = [r[0] for r in BOOKS]
        self.assertEqual(len(titles), len(set(titles)), "duplicate titles in seed")
        for _, title, _, _ in ISSUES:
            self.assertIn(title, titles)

    def test_stock_invariant(self):
        s = library.stats(self.db())
        self.assertEqual(s["copies"], s["available"] + s["issued"])

    def test_every_page_loads(self):
        for url in ["/", "/dashboard", "/books", "/books/1", "/return", "/student/login", "/admin/login",
                    "/api/books?q=python", "/api/book-options", "/api/issues"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class Catalogue(Base):
    def test_01_add_book_and_02_search_it(self):
        self.admin_login()
        r = self.client.post("/admin/books/new", data={
            "title": "Zebra Engineering Handbook", "author": "A. Tester", "publisher": "Test Press", "edition": "1st",
            "isbn": "", "category_id": 1, "subject": "Testing", "total_copies": 3, "description": "x",
            "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        found = self.client.get("/api/books?q=zebra").get_json()
        self.assertEqual(found["total"], 1)
        self.assertIn("Zebra Engineering Handbook", found["html"])

    def test_03_search_nonexistent(self):
        r = self.client.get("/api/books?q=qwertyuiop").get_json()
        self.assertEqual(r["total"], 0)

    def test_search_by_author_isbn_and_filters(self):
        self.assertGreaterEqual(self.client.get("/api/books?q=Norvig").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/books?q=978-0-13-110362-7").get_json()["total"], 1)
        total = self.client.get("/api/books?per_page=60").get_json()["total"]
        avail = self.client.get("/api/books?availability=available&per_page=60").get_json()["total"]
        self.assertLessEqual(avail, total)
        self.assertEqual(self.client.get("/api/books?q=%25").get_json()["total"], 0)  # '%' is literal, not a wildcard

    def test_pagination(self):
        a = self.client.get("/api/books?page=1&per_page=12").get_json()
        b = self.client.get("/api/books?page=2&per_page=12").get_json()
        self.assertTrue(a["has_more"])
        self.assertNotEqual(a["html"], b["html"])


class Circulation(Base):
    def test_04_issue_available_and_09_stock_updates(self):
        b = self.fresh(3)
        r = self.issue(book_id=b["id"])
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(self.book(b["title"])["available_copies"], 2)

    def test_05_issue_nonexistent_book(self):
        r = self.issue(book_id=99999)
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])

    def test_06_issue_unavailable_book(self):
        b = self.fresh(1)
        with self.db() as c:
            c.execute("UPDATE books SET available_copies = 0 WHERE id = ?", (b["id"],))
        r = self.issue(book_id=b["id"])
        self.assertEqual(r.status_code, 409)
        self.assertIn("No copies", r.get_json()["error"])

    def test_07_return_and_08_double_return(self):
        b = self.fresh(3)
        rec = self.issue(book_id=b["id"]).get_json()["record"]
        after_issue = self.book(b["title"])["available_copies"]
        r = self.client.post(f"/api/return/{rec['id']}", headers=self.h)      # request only
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.book(b["title"])["available_copies"], after_issue)
        self.client.post(f"/admin/returns/{rec['id']}/approve", data={"csrf_token": "tok"})
        self.assertEqual(self.book(b["title"])["available_copies"], after_issue + 1)
        again = self.client.post(f"/api/return/{rec['id']}", headers=self.h)
        self.assertEqual(again.status_code, 409)
        self.assertIn("already returned", again.get_json()["error"])
        self.assertEqual(self.book(b["title"])["available_copies"], after_issue + 1)

    def test_duplicate_active_issue_blocked(self):
        b = self.fresh(3)
        self.assertEqual(self.issue(book_id=b["id"]).status_code, 201)
        dup = self.issue(book_id=b["id"])
        self.assertEqual(dup.status_code, 409)
        self.assertIn("already has", dup.get_json()["error"])

    def test_14_multiple_transactions_same_book(self):
        b = self.fresh(3)
        for i, no in enumerate(["T001", "T002", "T003"]):
            self.assertEqual(self.issue(no=no, name=f"Tester {i}", book_id=b["id"]).status_code, 201)
        self.assertEqual(self.book(b["title"])["available_copies"], 0)
        self.assertEqual(self.issue(no="T004", name="Tester 4", book_id=b["id"]).status_code, 409)
        s = library.stats(self.db())
        self.assertEqual(s["copies"], s["available"] + s["issued"])

    def test_validation(self):
        b = self.fresh(3)["id"]
        self.assertEqual(self.issue(no="x", book_id=b).status_code, 400)
        self.assertEqual(self.issue(name="", book_id=b).status_code, 400)
        self.assertEqual(self.issue(book_id=b, issue_date="2999-01-01").status_code, 400)
        self.assertEqual(self.issue(book_id=b, issue_date="2026-01-10", due_date="2026-01-01").status_code, 400)
        self.assertEqual(self.issue(book_id=None).status_code, 400)

    def test_overdue_detection(self):
        rows, _ = library.list_issues(self.db(), status="overdue")
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(all(r["is_overdue"] for r in rows))

    def test_max_active_limit(self):
        ids = [self.fresh(3)["id"] for _ in range(6)]
        codes = [self.issue(no="LIM001", name="Limit Tester", book_id=i).status_code for i in ids]
        self.assertEqual(codes[:5], [201] * 5)
        self.assertEqual(codes[5], 409)

    def test_missing_csrf_rejected(self):
        self.admin_login()
        r = self.client.post("/api/issue", json={"student_no": "A", "student_name": "B", "book_id": 1})
        self.assertEqual(r.status_code, 400)


class Auth(Base):
    def test_11_invalid_login(self):
        r = self.client.post("/admin/login", data={"username": "admin", "password": "wrong", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/student/login", data={"enrollment_no": "CS24001", "password": "nope", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 401)

    def test_12_unauthorised_admin_access(self):
        for url in ["/admin/", "/admin/books", "/admin/books/new", "/admin/issues", "/admin/students",
                    "/admin/categories", "/admin/reports"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/admin/login", r.headers["Location"])
        r = self.client.post("/admin/books/1/delete", data={"csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNotNone(self.book("Clean Code"))

    def test_login_success_and_open_redirect_blocked(self):
        r = self.client.post("/admin/login", data={"username": "admin", "password": config.DEMO_ADMIN_PASSWORD,
                                                  "next": "https://evil.example", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil", r.headers["Location"])
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_student_portal(self):
        r = self.client.post("/student/login", data={"enrollment_no": "CS24001", "password": config.DEFAULT_STUDENT_PASSWORD,
                                                    "csrf_token": "tok"}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Aarav Sharma", r.data)
        self.assertIn(b"overdue", r.data)

    def test_password_is_hashed(self):
        h = self.db().execute("SELECT password_hash FROM admins").fetchone()[0]
        self.assertNotIn(config.DEMO_ADMIN_PASSWORD, h)


class AdminForms(Base):
    def test_13_form_validation(self):
        self.admin_login()
        r = self.client.post("/admin/books/new", data={"title": "", "author": "", "category_id": "", "total_copies": "-4",
                                                      "isbn": "123", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 422)
        for msg in (b"Title is required", b"Author is required", b"Choose a branch", b"whole number", b"check digit"):
            self.assertIn(msg, r.data)

    def test_delete_rules(self):
        self.admin_login()
        out = self.fresh(3)
        self.issue(book_id=out["id"])                        # one copy on loan
        self.client.post(f"/admin/books/{out['id']}/delete", data={"csrf_token": "tok"})
        self.assertEqual(self.book(out["title"])["is_archived"], 0)                  # refused
        hist = self.fresh(3, history=True)                   # history only, nothing out
        self.client.post(f"/admin/books/{hist['id']}/delete", data={"csrf_token": "tok"})
        self.assertEqual(self.book(hist["title"])["is_archived"], 1)                 # archived, history kept
        self.assertEqual(self.client.get(f"/books/{hist['id']}").status_code, 404)
        clean = self.fresh(3)
        self.client.post(f"/admin/books/{clean['id']}/delete", data={"csrf_token": "tok"})
        self.assertIsNone(self.book(clean["title"]))                                 # hard-deleted

    def test_stock_update_respects_loans(self):
        self.admin_login()
        b = self.fresh(8)
        self.issue(book_id=b["id"])                          # 1 copy out
        self.client.post(f"/admin/books/{b['id']}/stock", data={"total_copies": "12", "csrf_token": "tok"})
        self.assertEqual(self.book(b["title"])["available_copies"], 11)
        self.client.post(f"/admin/books/{b['id']}/stock", data={"total_copies": "0", "csrf_token": "tok"})
        self.assertEqual(self.book(b["title"])["total_copies"], 12)                  # refused: 1 copy is out

    def test_edit_and_categories(self):
        self.admin_login()
        b = self.fresh(3)
        r = self.client.post(f"/admin/books/{b['id']}/edit", data={
            "title": "Calculus (Early Transcendentals)", "author": "James Stewart", "publisher": "Cengage Learning",
            "edition": "", "isbn": "", "category_id": b["category_id"], "subject": "Calculus", "total_copies": 7,
            "description": "", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.book("Calculus (Early Transcendentals)")["total_copies"], 7)
        self.client.post("/admin/categories/add", data={"name": "Biotechnology", "short_name": "BIO", "csrf_token": "tok"})
        self.assertIsNotNone(self.db().execute("SELECT 1 FROM categories WHERE short_name = 'BIO'").fetchone())
        self.client.post("/admin/categories/1/delete", data={"csrf_token": "tok"})        # has books -> refused
        self.assertIsNotNone(self.db().execute("SELECT 1 FROM categories WHERE id = 1").fetchone())

    def test_admin_pages_render(self):
        self.admin_login()
        for url in ["/admin/", "/admin/books", "/admin/books?q=python&page=1", "/admin/books/new", "/admin/books/1/edit",
                    "/admin/issues", "/admin/issues?status=overdue", "/admin/students", "/admin/categories", "/admin/reports"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class Persistence(unittest.TestCase):
    def test_10_data_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.db")
            app1 = create_app({"DATABASE": path, "TESTING": True})
            c = app1.test_client()
            with c.session_transaction() as s:
                s["csrf_token"] = "t"
            conn0 = connect(path)
            conn0.execute("INSERT INTO books(title, author, category_id, total_copies, available_copies) "
                          "VALUES ('Zz Persist', 'A', 1, 3, 3)")
            bid = conn0.execute("SELECT id FROM books WHERE title = 'Zz Persist'").fetchone()[0]
            conn0.close()
            c.post("/admin/login", data={"username": config.DEMO_ADMIN_USERNAME,
                                         "password": config.DEMO_ADMIN_PASSWORD, "csrf_token": "t"})
            with c.session_transaction() as s:
                s["csrf_token"] = "t"
            r = c.post("/api/issue", headers={"X-CSRF-Token": "t"},
                       json={"student_no": "PERS01", "student_name": "Persist Test", "book_id": bid})
            self.assertEqual(r.status_code, 201)
            app2 = create_app({"DATABASE": path, "TESTING": True})        # simulated restart: must not reseed or wipe
            conn = connect(path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM students WHERE enrollment_no = 'PERS01'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT available_copies FROM books WHERE id = ?", (bid,)).fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM books").fetchone()[0], len(BOOKS) + 1)   # +1: the test book
            self.assertEqual(app2.test_client().get("/dashboard").status_code, 200)


class LateFine(Base):
    def loan(self, days_late):
        b = self.fresh(2)
        self.admin_login()
        from datetime import date, timedelta
        rec = self.issue(book_id=b["id"]).get_json()["record"]
        due = (date.today() - timedelta(days=days_late)).isoformat()
        issued = (date.today() - timedelta(days=days_late + 14)).isoformat()
        with self.db() as c:
            c.execute("UPDATE issue_records SET issue_date=?, due_date=? WHERE id=?", (issued, due, rec["id"]))
        return b, rec["id"]

    def test_fine_bill_pending_then_approved(self):
        b, rid = self.loan(4)
        r = self.client.post(f"/api/return/{rid}", headers=self.h).get_json()
        self.assertEqual(r["record"]["fine"], 20)
        self.assertIn("Pending admin approval", r["message"])
        row = self.db().execute("SELECT * FROM issue_records WHERE id=?", (rid,)).fetchone()
        self.assertIsNone(row["returned_at"]); self.assertEqual(row["fine_status"], "PENDING")
        self.assertEqual(self.book(b["title"])["available_copies"], 1)
        self.assertEqual(self.client.post(f"/api/return/{rid}", headers=self.h).status_code, 409)   # no double request
        self.assertIn("Pending return approvals", self.client.get("/admin/").get_data(as_text=True))
        self.client.post(f"/admin/returns/{rid}/approve", data={"csrf_token": "tok"})
        self.client.post(f"/admin/returns/{rid}/approve", data={"csrf_token": "tok"})   # second click: no extra stock
        row = self.db().execute("SELECT * FROM issue_records WHERE id=?", (rid,)).fetchone()
        self.assertIsNotNone(row["returned_at"]); self.assertEqual((row["fine_amount"], row["fine_status"]), (20, "PAID"))
        self.assertEqual(self.book(b["title"])["available_copies"], 2)

    def test_on_time_has_no_fine_and_approval_needs_admin(self):
        b, rid = self.loan(0)
        self.assertEqual(self.client.post(f"/api/return/{rid}", headers=self.h).get_json()["record"]["fine"], 0)
        self.client.post("/admin/logout", data={"csrf_token": "tok"})
        with self.client.session_transaction() as s:
            s["csrf_token"] = "tok"
        r = self.client.post(f"/admin/returns/{rid}/approve", data={"csrf_token": "tok"})
        self.assertIn("/admin/login", r.headers["Location"])
        self.assertEqual(self.book(b["title"])["available_copies"], 1)


class ShelfAndRequests(Base):
    def student_login(self, no="CS24001"):
        r = self.client.post("/student/login", data={"enrollment_no": no, "password": config.DEFAULT_STUDENT_PASSWORD,
                                                     "csrf_token": "tok"})
        with self.client.session_transaction() as s:
            s["csrf_token"] = "tok"
        self.assertEqual(r.status_code, 302)

    def request(self, book_id):
        return self.client.post(f"/student/request/{book_id}", data={"csrf_token": "tok"})

    def reqs(self, **where):
        return [dict(r) for r in self.db().execute("SELECT * FROM issue_requests")]

    def stock(self, b):
        return self.db().execute("SELECT available_copies FROM books WHERE id=?", (b["id"],)).fetchone()[0]

    def test_migration_adds_columns_and_keeps_data(self):
        import sqlite3
        path = os.path.join(self.tmp.name, "old.db")
        c = sqlite3.connect(path)
        c.executescript("""CREATE TABLE categories(id INTEGER PRIMARY KEY, name TEXT, short_name TEXT, hue INTEGER);
          CREATE TABLE books(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, author TEXT NOT NULL,
            publisher TEXT DEFAULT '', edition TEXT DEFAULT '', isbn TEXT DEFAULT '', description TEXT DEFAULT '',
            category_id INTEGER NOT NULL, subject TEXT DEFAULT '', total_copies INTEGER DEFAULT 1,
            available_copies INTEGER DEFAULT 1, is_archived INTEGER DEFAULT 0, added_at TEXT DEFAULT '');
          INSERT INTO categories VALUES (1,'X','X',1); INSERT INTO books(title,author,category_id) VALUES ('Old','A',1);""")
        c.commit(); c.close()
        from database import init_db
        for _ in range(2):                                        # repeatable
            init_db(path)
        conn = connect(path)
        row = conn.execute("SELECT * FROM books WHERE title='Old'").fetchone()
        self.assertNotEqual(row["shelf_block"], "")      # old book got a location
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM books").fetchone()[0], 1)

    def test_every_book_gets_a_location(self):
        n = self.db().execute("SELECT COUNT(*) FROM books WHERE shelf_block='' OR shelf_floor='' "
                              "OR shelf_rack='' OR shelf_number=''").fetchone()[0]
        self.assertEqual(n, 0)

    def test_admin_saves_shelf_and_student_sees_it(self):
        self.admin_login()
        b = self.fresh()
        form = {"title": b["title"], "author": "T. Author", "category_id": "1", "total_copies": "3",
                "shelf_block": "Block C", "shelf_floor": "2nd", "shelf_rack": "R-14", "shelf_number": "5",
                "csrf_token": "tok"}
        self.assertEqual(self.client.post(f"/admin/books/{b['id']}/edit", data=form).status_code, 302)
        html = self.client.get(f"/books/{b['id']}").get_data(as_text=True)
        for needle in ("Block C", "2nd", "R-14", "Locate book"):
            self.assertIn(needle, html)
        self.assertIn("Edit", self.client.get(f"/admin/books/{b['id']}/edit").get_data(as_text=True))

    def test_request_does_not_touch_stock_then_approve_once(self):
        b = self.fresh(2)
        self.student_login()
        self.assertEqual(self.request(b["id"]).status_code, 302)
        rows = self.reqs()
        self.assertEqual([r["status"] for r in rows], ["PENDING"])
        self.assertEqual(self.stock(b), 2)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM issue_records WHERE book_id=?", (b["id"],)).fetchone()[0], 0)
        self.request(b["id"])                                      # duplicate pending blocked
        self.assertEqual(len(self.reqs()), 1)
        self.assertIn("Pending", self.client.get("/student/").get_data(as_text=True))
        # student cannot approve
        rid = rows[0]["id"]
        r = self.client.post(f"/admin/requests/{rid}/approve", data={"csrf_token": "tok"})
        self.assertEqual(r.status_code, 302); self.assertIn("/admin/login", r.headers["Location"])
        self.assertEqual(self.stock(b), 2)
        # admin approves twice: stock deducted once
        self.client.post("/student/logout", data={"csrf_token": "tok"})
        with self.client.session_transaction() as s:
            s["csrf_token"] = "tok"
        self.admin_login()
        self.assertIn("Pending issue requests", self.client.get("/admin/").get_data(as_text=True))
        self.client.post(f"/admin/requests/{rid}/approve", data={"csrf_token": "tok"})
        self.client.post(f"/admin/requests/{rid}/approve", data={"csrf_token": "tok"})
        self.assertEqual(self.stock(b), 1)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM issue_records WHERE book_id=? AND returned_at IS NULL",
                                           (b["id"],)).fetchone()[0], 1)
        self.assertEqual(self.reqs()[0]["status"], "APPROVED")
        self.assertTrue(self.db().execute("SELECT 1 FROM activity_logs WHERE detail LIKE '%request approved%'").fetchone())
        self.client.post("/admin/logout", data={"csrf_token": "tok"})
        with self.client.session_transaction() as s:
            s["csrf_token"] = "tok"
        self.student_login()
        page = self.client.get("/student/").get_data(as_text=True)
        self.assertIn("Approved", page); self.assertIn(b["title"], page)

    def test_reject_keeps_stock_and_allows_rerequest(self):
        b = self.fresh(2)
        self.student_login()
        self.request(b["id"])
        rid = self.reqs()[0]["id"]
        self.client.post("/student/logout", data={"csrf_token": "tok"})
        with self.client.session_transaction() as s:
            s["csrf_token"] = "tok"
        self.admin_login()
        self.client.post(f"/admin/requests/{rid}/reject", data={"csrf_token": "tok"})
        self.client.post(f"/admin/requests/{rid}/approve", data={"csrf_token": "tok"})   # cannot revive
        self.assertEqual(self.stock(b), 2)
        self.assertEqual(self.reqs()[0]["status"], "REJECTED")

    def test_approval_revalidates_stock_and_limits(self):
        b = self.fresh(1)
        self.student_login("CS24001")
        self.request(b["id"])
        rid = self.reqs()[0]["id"]
        self.db().execute("UPDATE books SET available_copies = 0 WHERE id = ?", (b["id"],))   # stock vanished
        self.client.post("/student/logout", data={"csrf_token": "tok"})
        with self.client.session_transaction() as s:
            s["csrf_token"] = "tok"
        self.admin_login()
        self.client.post(f"/admin/requests/{rid}/approve", data={"csrf_token": "tok"})
        self.assertEqual(self.reqs()[0]["status"], "PENDING")
        self.assertEqual(self.stock(b), 0)

    def test_students_cannot_bypass_approval(self):
        b = self.fresh(2)
        self.student_login()
        r = self.client.post("/api/issue", headers=self.h,
                             json={"student_no": "CS24001", "student_name": "X Y", "book_id": b["id"]})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.stock(b), 2)
        self.assertEqual(self.client.get(f"/issue?book={b['id']}").status_code, 302)
        self.assertEqual(self.client.post(f"/student/request/{b['id']}").status_code, 400)   # CSRF enforced

    def test_request_requires_login(self):
        b = self.fresh(2)
        r = self.request(b["id"])
        self.assertEqual(r.status_code, 302); self.assertIn("/student/login", r.headers["Location"])
        self.assertEqual(self.reqs(), [])


if __name__ == "__main__":
    unittest.main()
