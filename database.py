"""SQLite layer for LIBRARYVERSE: schema, connections, transactions, seeding."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from flask import current_app, g
from werkzeug.security import generate_password_hash

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    short_name TEXT NOT NULL,
    hue        INTEGER NOT NULL DEFAULT 262 CHECK (hue BETWEEN 0 AND 360)
);

CREATE TABLE IF NOT EXISTS books (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    author           TEXT NOT NULL,
    publisher        TEXT NOT NULL DEFAULT '',
    edition          TEXT NOT NULL DEFAULT '',
    isbn             TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    category_id      INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    subject          TEXT NOT NULL DEFAULT '',
    total_copies     INTEGER NOT NULL DEFAULT 1 CHECK (total_copies >= 0),
    available_copies INTEGER NOT NULL DEFAULT 1 CHECK (available_copies >= 0),
    is_archived      INTEGER NOT NULL DEFAULT 0,
    shelf_block      TEXT NOT NULL DEFAULT '',
    shelf_floor      TEXT NOT NULL DEFAULT '',
    shelf_rack       TEXT NOT NULL DEFAULT '',
    shelf_number     TEXT NOT NULL DEFAULT '',
    added_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (available_copies <= total_copies)
);
CREATE INDEX IF NOT EXISTS idx_books_title    ON books(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_books_category ON books(category_id);
CREATE INDEX IF NOT EXISTS idx_books_added    ON books(added_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_books_isbn
    ON books(isbn) WHERE isbn <> '' AND is_archived = 0;

CREATE TABLE IF NOT EXISTS students (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_no TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS issue_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id)    ON DELETE RESTRICT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
    issue_date  TEXT NOT NULL,
    due_date    TEXT NOT NULL,
    returned_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    return_requested_at TEXT,
    fine_amount INTEGER NOT NULL DEFAULT 0,
    fine_status TEXT NOT NULL DEFAULT 'NONE',
    CHECK (due_date >= issue_date)
);
CREATE INDEX IF NOT EXISTS idx_issue_book    ON issue_records(book_id);
CREATE INDEX IF NOT EXISTS idx_issue_student ON issue_records(student_id);
CREATE INDEX IF NOT EXISTS idx_issue_due     ON issue_records(due_date) WHERE returned_at IS NULL;
-- One student cannot hold two active copies of the same title (enforced by the database itself).
CREATE UNIQUE INDEX IF NOT EXISTS idx_issue_active_unique
    ON issue_records(student_id, book_id) WHERE returned_at IS NULL;

CREATE TABLE IF NOT EXISTS issue_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id   INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
    book_id      INTEGER NOT NULL REFERENCES books(id)    ON DELETE RESTRICT,
    status       TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED')),
    requested_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    decided_at   TEXT,
    decided_by   TEXT,
    issue_id     INTEGER REFERENCES issue_records(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_req_student ON issue_requests(student_id);
CREATE INDEX IF NOT EXISTS idx_req_status  ON issue_requests(status);
-- A student can have only one pending request per book (enforced by the database itself).
CREATE UNIQUE INDEX IF NOT EXISTS idx_req_pending_unique
    ON issue_requests(student_id, book_id) WHERE status = 'PENDING';

CREATE TABLE IF NOT EXISTS activity_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON activity_logs(created_at);
"""


def connect(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # isolation_level=None -> we control transactions explicitly (BEGIN IMMEDIATE ... COMMIT)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn):
    """Write transaction. BEGIN IMMEDIATE takes the write lock up front so two
    simultaneous issues of the last copy cannot both succeed."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SHELF_COLUMNS = ("shelf_block", "shelf_floor", "shelf_rack", "shelf_number")


def migrate(conn):
    """Safe, repeatable upgrade for databases created before shelf locations / issue requests.
    Only adds missing columns; existing rows and data are never touched."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
    for col in SHELF_COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE books ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issue_records'").fetchone():
        have = {r["name"] for r in conn.execute("PRAGMA table_info(issue_records)")}
        for col, ddl in (("return_requested_at", "TEXT"), ("fine_amount", "INTEGER NOT NULL DEFAULT 0"),
                         ("fine_status", "TEXT NOT NULL DEFAULT 'NONE'")):
            if col not in have:
                conn.execute(f"ALTER TABLE issue_records ADD COLUMN {col} {ddl}")


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def assign_shelf_locations(conn):
    """Give every book that has no shelf location one, grouped by category.
    One block per category, three blocks per floor, 20 books per rack, 4 per shelf.
    Books that already have any location are left exactly as they are."""
    cats = [r["id"] for r in conn.execute("SELECT id FROM categories ORDER BY id")]
    with transaction(conn):
        for ci, cat_id in enumerate(cats):
            block = f"Block {chr(65 + ci % 26)}{'' if ci < 26 else ci // 26}"
            floor = _ordinal(ci // 3 + 1)
            books = conn.execute("SELECT id, shelf_block, shelf_floor, shelf_rack, shelf_number FROM books "
                                 "WHERE category_id = ? ORDER BY title COLLATE NOCASE, id", (cat_id,)).fetchall()
            for i, b in enumerate(books):
                if any(b[k] for k in SHELF_COLUMNS):
                    continue
                conn.execute("UPDATE books SET shelf_block=?, shelf_floor=?, shelf_rack=?, shelf_number=? WHERE id=?",
                             (block, floor, f"R-{i // 20 + 1}", str(i // 4 % 5 + 1), b["id"]))


def init_db(path):
    """Create tables and, on a brand-new database, load the demo data."""
    conn = connect(path)
    try:
        # Upgrade an old books table first, so the schema script below can rely on the new columns.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='books'").fetchone():
            migrate(conn)
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            seed(conn)
        elif conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
            _seed_admin(conn)
        assign_shelf_locations(conn)
    finally:
        conn.close()


def _seed_admin(conn):
    conn.execute(
        "INSERT INTO admins(username, password_hash) VALUES (?, ?)",
        (config.DEMO_ADMIN_USERNAME, generate_password_hash(config.DEMO_ADMIN_PASSWORD)),
    )


def seed(conn):
    from data.seed_books import BOOKS, CATEGORIES, ISSUES, STUDENTS

    now = datetime.now()
    fmt = "%Y-%m-%d %H:%M:%S"
    student_hash = generate_password_hash(config.DEFAULT_STUDENT_PASSWORD)

    with transaction(conn):
        _seed_admin(conn)
        cat_ids = {}
        for name, short, hue in CATEGORIES:
            cur = conn.execute(
                "INSERT INTO categories(name, short_name, hue) VALUES (?,?,?)", (name, short, hue)
            )
            cat_ids[short] = cur.lastrowid

        for i, (title, author, publisher, edition, isbn, cat, subject, copies, desc) in enumerate(BOOKS):
            # Spread 'added' dates over ~6 weeks so "recently added" is meaningful.
            added = (now - timedelta(days=(i * 7) % 45, minutes=i)).strftime(fmt)
            conn.execute(
                """INSERT INTO books(title, author, publisher, edition, isbn, description,
                                     category_id, subject, total_copies, available_copies, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (title, author, publisher, edition, isbn, desc, cat_ids[cat], subject, copies, copies, added),
            )

        student_ids = {}
        for no, name in STUDENTS:
            cur = conn.execute(
                "INSERT INTO students(enrollment_no, name, password_hash) VALUES (?,?,?)",
                (no, name, student_hash),
            )
            student_ids[no] = (cur.lastrowid, name)

        today = now.date()
        for no, title, issued_ago, returned_ago in ISSUES:
            book = conn.execute("SELECT id FROM books WHERE title = ?", (title,)).fetchone()
            if book is None:
                raise RuntimeError(f"Seed issue refers to unknown book: {title!r}")
            sid, sname = student_ids[no]
            issue = today - timedelta(days=issued_ago)
            due = issue + timedelta(days=config.LOAN_DAYS)
            ret = (today - timedelta(days=returned_ago)) if returned_ago is not None else None
            conn.execute(
                """INSERT INTO issue_records(book_id, student_id, issue_date, due_date, returned_at)
                   VALUES (?,?,?,?,?)""",
                (book["id"], sid, issue.isoformat(), due.isoformat(), ret.isoformat() if ret else None),
            )
            conn.execute(
                "INSERT INTO activity_logs(action, detail, actor, created_at) VALUES (?,?,?,?)",
                ("issued", f"{sname} borrowed “{title}”", "Circulation desk", f"{issue.isoformat()} 10:30:00"),
            )
            if ret:
                conn.execute(
                    "INSERT INTO activity_logs(action, detail, actor, created_at) VALUES (?,?,?,?)",
                    ("returned", f"{sname} returned “{title}”", "Circulation desk", f"{ret.isoformat()} 16:10:00"),
                )

        _seed_chaos(conn, student_hash, today, fmt)

        # Available copies = total copies minus copies currently out.
        conn.execute(
            """UPDATE books SET available_copies = total_copies - (
                   SELECT COUNT(*) FROM issue_records r
                   WHERE r.book_id = books.id AND r.returned_at IS NULL)"""
        )


def _seed_chaos(conn, student_hash, today, fmt):
    """Extra made-up students and a messy loan history so the demo does not look empty."""
    import random
    from data.seed_chaos import generate_loans, generate_students

    rng = random.Random(20260101)                       # fixed seed: same data on every fresh database
    have = conn.execute("SELECT enrollment_no, name FROM students").fetchall()
    extra = generate_students(rng, 44, [r[0] for r in have], [r[1] for r in have])
    for no, name, days in extra:
        reg = (datetime.now() - timedelta(days=days)).strftime(fmt)
        conn.execute("INSERT INTO students(enrollment_no, name, password_hash, created_at) VALUES (?,?,?,?)",
                     (no, name, student_hash, reg))

    sid = {r["enrollment_no"]: (r["id"], r["name"]) for r in conn.execute("SELECT * FROM students")}
    books = [tuple(r) for r in conn.execute(
        "SELECT b.id, b.title, b.total_copies, c.short_name FROM books b JOIN categories c ON c.id = b.category_id")]
    title = {b[0]: b[1] for b in books}
    held, out, pairs = {}, {}, set()
    for r in conn.execute("SELECT s.enrollment_no AS no, r.book_id AS bid FROM issue_records r "
                          "JOIN students s ON s.id = r.student_id WHERE r.returned_at IS NULL"):
        held[r["no"]] = held.get(r["no"], 0) + 1
        out[r["bid"]] = out.get(r["bid"], 0) + 1
        pairs.add((r["no"], r["bid"]))

    loans = generate_loans(rng, books, list(sid), held, out, pairs)
    now_str = datetime.now().strftime(fmt)

    def stamp(day, lo, hi):
        """Random library-hours time on `day`, never later than the present moment."""
        ts = f"{day.isoformat()} {rng.randint(lo, hi):02d}:{rng.randint(0, 59):02d}:00"
        return min(ts, now_str)
    for no, bid, issued_ago, returned_ago in loans:
        student_id, name = sid[no]
        issue = today - timedelta(days=issued_ago)
        due = issue + timedelta(days=config.LOAN_DAYS)
        ret = (today - timedelta(days=returned_ago)) if returned_ago is not None else None
        conn.execute("INSERT INTO issue_records(book_id, student_id, issue_date, due_date, returned_at) "
                     "VALUES (?,?,?,?,?)", (bid, student_id, issue.isoformat(), due.isoformat(),
                                            ret.isoformat() if ret else None))
        conn.execute("INSERT INTO activity_logs(action, detail, actor, created_at) VALUES (?,?,?,?)",
                     ("issued", f"{name} borrowed \u201c{title[bid]}\u201d", "Circulation desk", stamp(issue, 10, 19)))
        if ret:
            conn.execute("INSERT INTO activity_logs(action, detail, actor, created_at) VALUES (?,?,?,?)",
                         ("returned", f"{name} returned \u201c{title[bid]}\u201d", "Circulation desk", stamp(ret, 10, 20)))
