"""All library rules live here so routes stay thin and everything is testable."""
import re
import sqlite3
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import config
from data.seed_books import FEATURED_TITLES
from database import transaction


class LibraryError(Exception):
    """A problem the user can fix. `field` lets the UI highlight the input."""

    def __init__(self, message, status=400, field=None):
        super().__init__(message)
        self.message, self.status, self.field = message, status, field


# --------------------------------------------------------------------------- helpers
def today():
    return date.today()


def normalize_isbn(value):
    return re.sub(r"[\s-]", "", value or "").upper()


def is_valid_isbn(value):
    v = normalize_isbn(value)
    if len(v) == 13 and v.isdigit():
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(v[:12]))
        return (10 - total % 10) % 10 == int(v[12])
    if re.fullmatch(r"\d{9}[\dX]", v):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(v))
        return total % 11 == 0
    return False


def _like(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def log(conn, action, detail, actor="system"):
    conn.execute("INSERT INTO activity_logs(action, detail, actor) VALUES (?,?,?)", (action, detail, actor))


def _parse_date(value, field, label):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise LibraryError(f"{label} must be a valid date.", field=field)


def fine_for(due_date, returned_on):
    """Fine in rupees: FINE_PER_DAY for every day after the due date."""
    late = (returned_on - date.fromisoformat(due_date)).days
    return max(late, 0) * config.FINE_PER_DAY


def decorate_issue(row, ref_day=None):
    """Add overdue/days-left info to an issue record row."""
    ref_day = ref_day or today()
    d = dict(row)
    due = date.fromisoformat(d["due_date"])
    d["return_pending"] = d["returned_at"] is None and bool(d.get("return_requested_at"))
    if d["returned_at"] is None:
        # A pending return is billed as of the day it was requested; otherwise show the fine accrued today.
        bill_day = date.fromisoformat(d["return_requested_at"][:10]) if d["return_pending"] else ref_day
        d["fine_now"] = fine_for(d["due_date"], bill_day)
        d["late_days_now"] = max((bill_day - due).days, 0)
        delta = (due - ref_day).days
        d.update(status="overdue" if delta < 0 else "active", is_overdue=delta < 0,
                 days_overdue=max(-delta, 0), days_left=max(delta, 0), days_late=0)
    else:
        late = (date.fromisoformat(d["returned_at"]) - due).days
        d["fine_now"] = d["fine_amount"]
        d["late_days_now"] = 0
        d.update(status="returned", is_overdue=False, days_overdue=0, days_left=0, days_late=max(late, 0))
    return d


# --------------------------------------------------------------------------- catalogue
BOOK_SELECT = """
    SELECT b.*, c.name AS category_name, c.short_name AS category_short, c.hue AS hue
    FROM books b JOIN categories c ON c.id = b.category_id
"""

SORTS = {
    "title": "b.title COLLATE NOCASE ASC",
    "title_desc": "b.title COLLATE NOCASE DESC",
    "newest": "b.added_at DESC, b.id DESC",
    "popular": "(SELECT COUNT(*) FROM issue_records r WHERE r.book_id = b.id) DESC, b.title COLLATE NOCASE ASC",
    "available": "b.available_copies DESC, b.title COLLATE NOCASE ASC",
}


def search_books(conn, q="", category=None, subject="", availability="", sort="title", page=1,
                 per_page=config.PER_PAGE):
    where, params = ["b.is_archived = 0"], []
    q = (q or "").strip()
    if q:
        clauses = ["b.title LIKE ? ESCAPE '\\'", "b.author LIKE ? ESCAPE '\\'",
                   "b.publisher LIKE ? ESCAPE '\\'", "b.subject LIKE ? ESCAPE '\\'"]
        params += [_like(q)] * 4
        isbn_q = normalize_isbn(q)
        if isbn_q:
            clauses.append("REPLACE(b.isbn, '-', '') LIKE ? ESCAPE '\\'")
            params.append(_like(isbn_q))
        where.append("(" + " OR ".join(clauses) + ")")
    if category:
        where.append("b.category_id = ?")
        params.append(int(category))
    if subject:
        where.append("b.subject = ? COLLATE NOCASE")
        params.append(subject)
    if availability == "available":
        where.append("b.available_copies > 0")
    elif availability == "unavailable":
        where.append("b.available_copies = 0")

    where_sql = " WHERE " + " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM books b{where_sql}", params).fetchone()[0]
    page = max(int(page or 1), 1)
    per_page = min(max(int(per_page or config.PER_PAGE), 1), 60)
    rows = conn.execute(
        f"{BOOK_SELECT}{where_sql} ORDER BY {SORTS.get(sort, SORTS['title'])} LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    pages = max((total + per_page - 1) // per_page, 1)
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "pages": pages,
            "has_more": page < pages}


def get_book(conn, book_id):
    row = conn.execute(f"{BOOK_SELECT} WHERE b.id = ? AND b.is_archived = 0", (book_id,)).fetchone()
    if row is None:
        return None
    book = dict(row)
    book["times_issued"] = conn.execute(
        "SELECT COUNT(*) FROM issue_records WHERE book_id = ?", (book_id,)).fetchone()[0]
    book["issued_now"] = book["total_copies"] - book["available_copies"]
    book["has_location"] = any(book[k] for k in ("shelf_block", "shelf_floor", "shelf_rack", "shelf_number"))
    return book


def related_books(conn, book, limit=4):
    rows = conn.execute(
        f"{BOOK_SELECT} WHERE b.category_id = ? AND b.id <> ? AND b.is_archived = 0 "
        "ORDER BY b.available_copies > 0 DESC, b.title COLLATE NOCASE LIMIT ?",
        (book["category_id"], book["id"], limit)).fetchall()
    return [dict(r) for r in rows]


def subjects(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT subject FROM books WHERE is_archived = 0 AND subject <> '' "
        "ORDER BY subject COLLATE NOCASE")]


def categories(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE")]


def recent_books(conn, limit=6):
    rows = conn.execute(f"{BOOK_SELECT} WHERE b.is_archived = 0 ORDER BY b.added_at DESC, b.id DESC LIMIT ?",
                        (limit,)).fetchall()
    return [dict(r) for r in rows]


def popular_books(conn, limit=5):
    rows = conn.execute(
        f"""SELECT b.*, c.name AS category_name, c.short_name AS category_short, c.hue AS hue,
                   COUNT(r.id) AS times_issued
            FROM books b JOIN categories c ON c.id = b.category_id
            JOIN issue_records r ON r.book_id = b.id
            WHERE b.is_archived = 0
            GROUP BY b.id ORDER BY times_issued DESC, b.title COLLATE NOCASE LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def featured_books(conn, limit=6):
    out = []
    for title in FEATURED_TITLES:
        row = conn.execute(f"{BOOK_SELECT} WHERE b.title = ? AND b.is_archived = 0", (title,)).fetchone()
        if row:
            out.append(dict(row))
    seen = {b["id"] for b in out}
    for b in recent_books(conn, limit * 2):          # top up if featured titles were deleted
        if len(out) >= limit:
            break
        if b["id"] not in seen:
            out.append(b)
    return out[:limit]


def book_options(conn, q="", limit=8):
    q = (q or "").strip()
    params, where = [], "b.is_archived = 0"
    if q:
        where += (" AND (b.title LIKE ? ESCAPE '\\' OR b.author LIKE ? ESCAPE '\\'"
                  " OR REPLACE(b.isbn,'-','') LIKE ? ESCAPE '\\')")
        params = [_like(q), _like(q), _like(normalize_isbn(q) or q)]
    rows = conn.execute(
        f"SELECT b.id, b.title, b.author, b.available_copies, b.total_copies FROM books b "
        f"WHERE {where} ORDER BY b.available_copies > 0 DESC, b.title COLLATE NOCASE LIMIT ?",
        params + [limit]).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- statistics
def stats(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS titles, COALESCE(SUM(total_copies),0) AS copies, "
        "COALESCE(SUM(available_copies),0) AS available FROM books WHERE is_archived = 0").fetchone()
    issued = conn.execute("SELECT COUNT(*) FROM issue_records WHERE returned_at IS NULL").fetchone()[0]
    overdue = conn.execute("SELECT COUNT(*) FROM issue_records WHERE returned_at IS NULL AND due_date < ?",
                           (today().isoformat(),)).fetchone()[0]
    return {
        "titles": row["titles"], "copies": row["copies"], "available": row["available"], "issued": issued,
        "overdue": overdue,
        "students": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        "categories": conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
        "transactions": conn.execute("SELECT COUNT(*) FROM issue_records").fetchone()[0],
    }


def category_distribution(conn):
    rows = conn.execute(
        """SELECT c.id, c.name, c.short_name, c.hue, COUNT(b.id) AS titles,
                  COALESCE(SUM(b.total_copies),0) AS copies, COALESCE(SUM(b.available_copies),0) AS available
           FROM categories c LEFT JOIN books b ON b.category_id = c.id AND b.is_archived = 0
           GROUP BY c.id ORDER BY titles DESC, c.name COLLATE NOCASE""").fetchall()
    return [dict(r) | {"issued": r["copies"] - r["available"]} for r in rows]


def recent_activity(conn, limit=8):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM activity_logs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))]


ISSUE_SELECT = """
    SELECT r.*, s.name AS student_name, s.enrollment_no, b.title AS book_title, b.author AS book_author,
           c.hue AS hue, c.short_name AS category_short
    FROM issue_records r
    JOIN students s ON s.id = r.student_id
    JOIN books b ON b.id = r.book_id
    JOIN categories c ON c.id = b.category_id
"""


def list_issues(conn, q="", status="all", limit=50, offset=0, student_id=None):
    where, params = [], []
    q = (q or "").strip()
    if q:
        where.append("(s.name LIKE ? ESCAPE '\\' OR s.enrollment_no LIKE ? ESCAPE '\\' "
                     "OR b.title LIKE ? ESCAPE '\\' OR b.author LIKE ? ESCAPE '\\')")
        params += [_like(q)] * 4
    if student_id:
        where.append("r.student_id = ?")
        params.append(student_id)
    if status == "active":
        where.append("r.returned_at IS NULL")
    elif status == "overdue":
        where.append("r.returned_at IS NULL AND r.due_date < ?")
        params.append(today().isoformat())
    elif status == "returned":
        where.append("r.returned_at IS NOT NULL")
    elif status == "pending_return":
        where.append("r.returned_at IS NULL AND r.return_requested_at IS NOT NULL")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM issue_records r JOIN students s ON s.id = r.student_id "
        f"JOIN books b ON b.id = r.book_id{where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"{ISSUE_SELECT}{where_sql} ORDER BY (r.returned_at IS NOT NULL), "
        "CASE WHEN r.returned_at IS NULL THEN r.due_date END ASC, r.returned_at DESC, r.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    ref = today()
    return [decorate_issue(r, ref) for r in rows], total


def recent_issues(conn, limit=5):
    rows = conn.execute(f"{ISSUE_SELECT} ORDER BY r.issue_date DESC, r.id DESC LIMIT ?", (limit,)).fetchall()
    ref = today()
    return [decorate_issue(r, ref) for r in rows]


def most_issued(conn, limit=10):
    return popular_books(conn, limit)


# --------------------------------------------------------------------------- circulation
def issue_book(conn, student_no, student_name, book_id, issue_date=None, due_date=None, actor="Circulation desk"):
    student_no = (student_no or "").strip()
    student_name = " ".join((student_name or "").split())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]{2,19}", student_no):
        raise LibraryError("Enter a student ID of 3–20 letters or digits (for example CS24001).", field="student_no")
    if not 2 <= len(student_name) <= 80:
        raise LibraryError("Enter the student's full name (2–80 characters).", field="student_name")
    try:
        book_id = int(book_id)
    except (TypeError, ValueError):
        raise LibraryError("Choose a book to issue.", field="book_id")

    ref = today()
    issued_on = _parse_date(issue_date or ref.isoformat(), "issue_date", "Issue date")
    due_on = _parse_date(due_date or (issued_on + timedelta(days=config.LOAN_DAYS)).isoformat(),
                         "due_date", "Due date")
    if issued_on > ref:
        raise LibraryError("Issue date cannot be in the future.", field="issue_date")
    if (ref - issued_on).days > config.MAX_BACKDATE_DAYS:
        raise LibraryError("Issue date is too far in the past.", field="issue_date")
    if due_on < issued_on:
        raise LibraryError("Due date must be on or after the issue date.", field="due_date")
    if (due_on - issued_on).days > config.MAX_LOAN_DAYS:
        raise LibraryError(f"Loans cannot exceed {config.MAX_LOAN_DAYS} days.", field="due_date")

    try:
        with transaction(conn):
            book = conn.execute("SELECT * FROM books WHERE id = ? AND is_archived = 0", (book_id,)).fetchone()
            if book is None:
                raise LibraryError("That book is not in the catalogue.", 404, "book_id")
            if book["available_copies"] <= 0:
                raise LibraryError(f"No copies of “{book['title']}” are available right now.", 409, "book_id")

            student = conn.execute("SELECT * FROM students WHERE enrollment_no = ?", (student_no,)).fetchone()
            new_student = student is None
            if new_student:
                cur = conn.execute(
                    "INSERT INTO students(enrollment_no, name, password_hash) VALUES (?,?,?)",
                    (student_no.upper(), student_name, generate_password_hash(config.DEFAULT_STUDENT_PASSWORD)))
                student = conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
            else:
                if conn.execute("SELECT 1 FROM issue_records WHERE student_id = ? AND book_id = ? "
                                "AND returned_at IS NULL", (student["id"], book_id)).fetchone():
                    raise LibraryError(f"{student['name']} already has “{book['title']}” on loan.", 409, "book_id")
                held = conn.execute("SELECT COUNT(*) FROM issue_records WHERE student_id = ? "
                                    "AND returned_at IS NULL", (student["id"],)).fetchone()[0]
                if held >= config.MAX_ACTIVE_ISSUES:
                    raise LibraryError(f"{student['name']} already holds {held} books, the maximum allowed.",
                                       409, "student_no")

            cur = conn.execute(
                "INSERT INTO issue_records(book_id, student_id, issue_date, due_date) VALUES (?,?,?,?)",
                (book_id, student["id"], issued_on.isoformat(), due_on.isoformat()))
            moved = conn.execute(
                "UPDATE books SET available_copies = available_copies - 1 WHERE id = ? AND available_copies > 0",
                (book_id,)).rowcount
            if moved != 1:
                raise LibraryError("The last copy was just taken. Please choose another book.", 409, "book_id")
            log(conn, "issued", f"{student['name']} borrowed “{book['title']}”", actor)
            if new_student:
                log(conn, "student", f"{student['name']} ({student['enrollment_no']}) was registered", actor)
            record_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise LibraryError("That issue conflicts with an existing record. Please refresh and try again.", 409)

    left = conn.execute("SELECT available_copies, total_copies FROM books WHERE id = ?", (book_id,)).fetchone()
    return {"id": record_id, "student_name": student["name"], "enrollment_no": student["enrollment_no"],
            "book_id": book_id, "book_title": book["title"], "issue_date": issued_on.isoformat(),
            "due_date": due_on.isoformat(), "available": left["available_copies"], "total": left["total_copies"],
            "new_student": new_student}


def request_return(conn, issue_id):
    """Student/desk asks to return a book. Calculates the fine bill; the loan stays open until an admin approves."""
    try:
        issue_id = int(issue_id)
    except (TypeError, ValueError):
        raise LibraryError("Unknown issue record.", 404)
    with transaction(conn):
        rec = conn.execute(
            "SELECT r.*, b.title, s.name AS student_name FROM issue_records r "
            "JOIN books b ON b.id = r.book_id JOIN students s ON s.id = r.student_id WHERE r.id = ?",
            (issue_id,)).fetchone()
        if rec is None:
            raise LibraryError("That issue record does not exist.", 404)
        if rec["returned_at"] is not None:
            raise LibraryError(f"“{rec['title']}” was already returned on {rec['returned_at']}.", 409)
        if rec["return_requested_at"]:
            raise LibraryError(f"A return for “{rec['title']}” is already waiting for admin approval.", 409)
        ref = today()
        fine = fine_for(rec["due_date"], ref)
        late = max((ref - date.fromisoformat(rec["due_date"])).days, 0)
        conn.execute("UPDATE issue_records SET return_requested_at = datetime('now','localtime'), "
                     "fine_amount = ?, fine_status = ? WHERE id = ?",
                     (fine, "PENDING" if fine else "NONE", issue_id))
        log(conn, "return_requested", f"{rec['student_name']} asked to return “{rec['title']}”"
            + (f" (fine ₹{fine}, {late} day{'s' if late != 1 else ''} late)" if fine else ""), "Student")
    return {"id": issue_id, "book_title": rec["title"], "student_name": rec["student_name"],
            "due_date": rec["due_date"], "days_late": late, "fine": fine, "fine_per_day": config.FINE_PER_DAY}


def approve_return(conn, issue_id, actor):
    """Admin approves the return and fine bill: book is returned, stock goes up, fine is saved as paid."""
    rec = conn.execute("SELECT return_requested_at, fine_amount, returned_at FROM issue_records WHERE id = ?",
                       (issue_id,)).fetchone()
    if rec is None:
        raise LibraryError("That issue record does not exist.", 404)
    if rec["returned_at"] is None and not rec["return_requested_at"]:
        raise LibraryError("No return has been requested for this loan.", 409)
    return return_book(conn, issue_id, actor, returned_on=date.fromisoformat(rec["return_requested_at"][:10])
                       if rec["return_requested_at"] else None, fine=rec["fine_amount"])


def return_book(conn, issue_id, actor="Circulation desk", returned_on=None, fine=0):
    try:
        issue_id = int(issue_id)
    except (TypeError, ValueError):
        raise LibraryError("Unknown issue record.", 404)
    with transaction(conn):
        rec = conn.execute(
            "SELECT r.*, b.title, s.name AS student_name FROM issue_records r "
            "JOIN books b ON b.id = r.book_id JOIN students s ON s.id = r.student_id WHERE r.id = ?",
            (issue_id,)).fetchone()
        if rec is None:
            raise LibraryError("That issue record does not exist.", 404)
        if rec["returned_at"] is not None:
            raise LibraryError(f"“{rec['title']}” was already returned on {rec['returned_at']}.", 409)
        ref = returned_on or today()
        changed = conn.execute("UPDATE issue_records SET returned_at = ?, fine_amount = ?, fine_status = ? "
                               "WHERE id = ? AND returned_at IS NULL",
                               (ref.isoformat(), fine, "PAID" if fine else "NONE", issue_id)).rowcount
        if changed != 1:
            raise LibraryError("This book was just returned by someone else.", 409)
        conn.execute("UPDATE books SET available_copies = available_copies + 1 WHERE id = ?", (rec["book_id"],))
        late = max((ref - date.fromisoformat(rec["due_date"])).days, 0)
        log(conn, "returned", f"{rec['student_name']} returned “{rec['title']}”"
            + (f" ({late} day{'s' if late != 1 else ''} late, fine ₹{fine} paid)" if late else ""), actor)
    left = conn.execute("SELECT available_copies, total_copies FROM books WHERE id = ?",
                        (rec["book_id"],)).fetchone()
    return {"id": issue_id, "book_title": rec["title"], "student_name": rec["student_name"],
            "days_late": late, "fine": fine, "available": left["available_copies"], "total": left["total_copies"]}


# --------------------------------------------------------------------------- issue requests
REQUEST_SELECT = """
    SELECT q.*, s.name AS student_name, s.enrollment_no, b.title AS book_title, b.author AS book_author,
           b.available_copies, b.total_copies, c.hue AS hue,
           i.issue_date AS issue_date, i.due_date AS due_date
    FROM issue_requests q
    JOIN students s ON s.id = q.student_id
    JOIN books b ON b.id = q.book_id
    JOIN categories c ON c.id = b.category_id
    LEFT JOIN issue_records i ON i.id = q.issue_id
"""


def request_book(conn, student_id, book_id):
    """Student asks for a book. Creates a PENDING request only; stock and loans are untouched."""
    try:
        book_id = int(book_id)
    except (TypeError, ValueError):
        raise LibraryError("Choose a book to request.", 400, "book_id")
    try:
        with transaction(conn):
            student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
            if student is None:
                raise LibraryError("Student account not found.", 404)
            book = conn.execute("SELECT * FROM books WHERE id = ? AND is_archived = 0", (book_id,)).fetchone()
            if book is None:
                raise LibraryError("That book is not in the catalogue.", 404, "book_id")
            if book["available_copies"] <= 0:
                raise LibraryError(f"No copies of “{book['title']}” are available right now.", 409, "book_id")
            if conn.execute("SELECT 1 FROM issue_records WHERE student_id = ? AND book_id = ? AND returned_at IS NULL",
                            (student_id, book_id)).fetchone():
                raise LibraryError(f"You already have “{book['title']}” on loan.", 409, "book_id")
            if conn.execute("SELECT 1 FROM issue_requests WHERE student_id = ? AND book_id = ? AND status = 'PENDING'",
                            (student_id, book_id)).fetchone():
                raise LibraryError(f"You already have a pending request for “{book['title']}”.", 409, "book_id")
            held = conn.execute("SELECT COUNT(*) FROM issue_records WHERE student_id = ? AND returned_at IS NULL",
                                (student_id,)).fetchone()[0]
            if held >= config.MAX_ACTIVE_ISSUES:
                raise LibraryError(f"You already hold {held} books, the maximum allowed.", 409)
            cur = conn.execute("INSERT INTO issue_requests(student_id, book_id) VALUES (?,?)", (student_id, book_id))
            log(conn, "request", f"{student['name']} requested “{book['title']}”", f"Student ({student['enrollment_no']})")
    except sqlite3.IntegrityError:
        raise LibraryError("You already have a pending request for this book.", 409, "book_id")
    return {"id": cur.lastrowid, "book_title": book["title"]}


def list_requests(conn, status=None, student_id=None, limit=100):
    where, params = [], []
    if status:
        where.append("q.status = ?")
        params.append(status)
    if student_id:
        where.append("q.student_id = ?")
        params.append(student_id)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order = "q.requested_at ASC, q.id ASC" if status == "PENDING" else "q.id DESC"
    rows = conn.execute(f"{REQUEST_SELECT}{where_sql} ORDER BY {order} LIMIT ?", params + [limit]).fetchall()
    return [dict(r) for r in rows]


def count_pending_requests(conn):
    return conn.execute("SELECT COUNT(*) FROM issue_requests WHERE status = 'PENDING'").fetchone()[0]


def approve_request(conn, request_id, actor):
    """Atomically validate and approve: creates the issue record and deducts stock exactly once."""
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        raise LibraryError("Unknown request.", 404)
    ref = today()
    due = ref + timedelta(days=config.LOAN_DAYS)
    try:
        with transaction(conn):
            req = conn.execute(REQUEST_SELECT + " WHERE q.id = ?", (request_id,)).fetchone()
            if req is None:
                raise LibraryError("That request does not exist.", 404)
            if req["status"] != "PENDING":
                raise LibraryError(f"This request was already {req['status'].lower()}.", 409)
            book = conn.execute("SELECT * FROM books WHERE id = ? AND is_archived = 0", (req["book_id"],)).fetchone()
            if book is None:
                raise LibraryError("That book is no longer in the catalogue.", 409)
            if book["available_copies"] <= 0:
                raise LibraryError(f"No copies of “{book['title']}” are available now. Reject the request or wait for a return.", 409)
            if conn.execute("SELECT 1 FROM issue_records WHERE student_id = ? AND book_id = ? AND returned_at IS NULL",
                            (req["student_id"], req["book_id"])).fetchone():
                raise LibraryError(f"{req['student_name']} already has “{book['title']}” on loan.", 409)
            held = conn.execute("SELECT COUNT(*) FROM issue_records WHERE student_id = ? AND returned_at IS NULL",
                                (req["student_id"],)).fetchone()[0]
            if held >= config.MAX_ACTIVE_ISSUES:
                raise LibraryError(f"{req['student_name']} already holds {held} books, the maximum allowed.", 409)
            cur = conn.execute("INSERT INTO issue_records(book_id, student_id, issue_date, due_date) VALUES (?,?,?,?)",
                               (req["book_id"], req["student_id"], ref.isoformat(), due.isoformat()))
            if conn.execute("UPDATE books SET available_copies = available_copies - 1 "
                            "WHERE id = ? AND available_copies > 0", (req["book_id"],)).rowcount != 1:
                raise LibraryError("The last copy was just taken.", 409)
            if conn.execute("UPDATE issue_requests SET status = 'APPROVED', decided_at = datetime('now','localtime'), "
                            "decided_by = ?, issue_id = ? WHERE id = ? AND status = 'PENDING'",
                            (actor, cur.lastrowid, request_id)).rowcount != 1:
                raise LibraryError("This request was just handled by someone else.", 409)
            log(conn, "issued", f"{req['student_name']} borrowed “{book['title']}” (request approved)", actor)
    except sqlite3.IntegrityError:
        raise LibraryError("That issue conflicts with an existing record. Please refresh and try again.", 409)
    return {"student_name": req["student_name"], "book_title": book["title"], "due_date": due.isoformat()}


def reject_request(conn, request_id, actor):
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        raise LibraryError("Unknown request.", 404)
    with transaction(conn):
        req = conn.execute(REQUEST_SELECT + " WHERE q.id = ?", (request_id,)).fetchone()
        if req is None:
            raise LibraryError("That request does not exist.", 404)
        if req["status"] != "PENDING":
            raise LibraryError(f"This request was already {req['status'].lower()}.", 409)
        conn.execute("UPDATE issue_requests SET status = 'REJECTED', decided_at = datetime('now','localtime'), "
                     "decided_by = ? WHERE id = ? AND status = 'PENDING'", (actor, request_id))
        log(conn, "rejected", f"Request by {req['student_name']} for “{req['book_title']}” was rejected", actor)
    return {"student_name": req["student_name"], "book_title": req["book_title"]}


# --------------------------------------------------------------------------- students
def get_student(conn, enrollment_no):
    return conn.execute("SELECT * FROM students WHERE enrollment_no = ?", ((enrollment_no or "").strip(),)).fetchone()


def list_students(conn):
    rows = conn.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM issue_records r WHERE r.student_id = s.id AND r.returned_at IS NULL) AS active,
                  (SELECT COUNT(*) FROM issue_records r WHERE r.student_id = s.id AND r.returned_at IS NULL
                       AND r.due_date < ?) AS overdue,
                  (SELECT COUNT(*) FROM issue_records r WHERE r.student_id = s.id) AS total_issued
           FROM students s ORDER BY s.name COLLATE NOCASE""", (today().isoformat(),)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- admin: books
def validate_book_form(conn, form, book_id=None):
    clean, errors = {}, {}

    def text(key, label, maxlen, required=False):
        value = " ".join((form.get(key) or "").split())
        if required and not value:
            errors[key] = f"{label} is required."
        elif len(value) > maxlen:
            errors[key] = f"{label} must be {maxlen} characters or fewer."
        clean[key] = value

    text("title", "Title", 200, True)
    text("author", "Author", 200, True)
    text("publisher", "Publisher", 120)
    text("edition", "Edition", 40)
    text("subject", "Subject", 80)
    text("shelf_block", "Block / section", 40)
    text("shelf_floor", "Floor", 20)
    text("shelf_rack", "Rack number", 20)
    text("shelf_number", "Shelf number", 20)
    clean["description"] = (form.get("description") or "").strip()
    if len(clean["description"]) > 2000:
        errors["description"] = "Description must be 2000 characters or fewer."

    isbn = (form.get("isbn") or "").strip()
    if isbn:
        if not is_valid_isbn(isbn):
            errors["isbn"] = "ISBN must be a valid 10 or 13 digit ISBN (the check digit did not match)."
        else:
            isbn = normalize_isbn(isbn)
            dup = conn.execute("SELECT id, title FROM books WHERE isbn = ? AND is_archived = 0 AND id <> ?",
                               (isbn, book_id or 0)).fetchone()
            if dup:
                errors["isbn"] = f"This ISBN already belongs to “{dup['title']}”."
    clean["isbn"] = isbn

    try:
        cat = int(form.get("category_id") or 0)
        if not conn.execute("SELECT 1 FROM categories WHERE id = ?", (cat,)).fetchone():
            raise ValueError
        clean["category_id"] = cat
    except ValueError:
        errors["category_id"] = "Choose a branch / category."
        clean["category_id"] = None

    try:
        copies = int((form.get("total_copies") or "").strip())
        if not 0 <= copies <= 999:
            raise ValueError
        clean["total_copies"] = copies
    except ValueError:
        errors["total_copies"] = "Total copies must be a whole number from 0 to 999."
        clean["total_copies"] = form.get("total_copies", "")
    return clean, errors


def create_book(conn, data, actor):
    with transaction(conn):
        cur = conn.execute(
            """INSERT INTO books(title, author, publisher, edition, isbn, description, category_id, subject,
                                 total_copies, available_copies,
                                 shelf_block, shelf_floor, shelf_rack, shelf_number)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data["title"], data["author"], data["publisher"], data["edition"], data["isbn"],
             data["description"], data["category_id"], data["subject"], data["total_copies"], data["total_copies"],
             data["shelf_block"], data["shelf_floor"], data["shelf_rack"], data["shelf_number"]))
        log(conn, "book_added", f"“{data['title']}” was added to the catalogue ({data['total_copies']} copies)", actor)
    return cur.lastrowid


def _active_count(conn, book_id):
    return conn.execute("SELECT COUNT(*) FROM issue_records WHERE book_id = ? AND returned_at IS NULL",
                        (book_id,)).fetchone()[0]


def update_book(conn, book_id, data, actor):
    with transaction(conn):
        if conn.execute("SELECT 1 FROM books WHERE id = ? AND is_archived = 0", (book_id,)).fetchone() is None:
            raise LibraryError("That book no longer exists.", 404)
        out = _active_count(conn, book_id)
        if data["total_copies"] < out:
            raise LibraryError(f"{out} cop{'y is' if out == 1 else 'ies are'} currently issued, so total copies "
                               f"cannot be below {out}.", 409, "total_copies")
        conn.execute(
            """UPDATE books SET title=?, author=?, publisher=?, edition=?, isbn=?, description=?, category_id=?,
                      subject=?, total_copies=?, available_copies=?,
                      shelf_block=?, shelf_floor=?, shelf_rack=?, shelf_number=? WHERE id=?""",
            (data["title"], data["author"], data["publisher"], data["edition"], data["isbn"], data["description"],
             data["category_id"], data["subject"], data["total_copies"], data["total_copies"] - out,
             data["shelf_block"], data["shelf_floor"], data["shelf_rack"], data["shelf_number"], book_id))
        log(conn, "book_edited", f"“{data['title']}” was updated", actor)


def set_stock(conn, book_id, total, actor):
    try:
        total = int(total)
        if not 0 <= total <= 999:
            raise ValueError
    except (TypeError, ValueError):
        raise LibraryError("Total copies must be a whole number from 0 to 999.", field="total_copies")
    with transaction(conn):
        book = conn.execute("SELECT title FROM books WHERE id = ? AND is_archived = 0", (book_id,)).fetchone()
        if book is None:
            raise LibraryError("That book no longer exists.", 404)
        out = _active_count(conn, book_id)
        if total < out:
            raise LibraryError(f"{out} cop{'y is' if out == 1 else 'ies are'} currently issued, so total copies "
                               f"cannot be below {out}.", 409, "total_copies")
        conn.execute("UPDATE books SET total_copies = ?, available_copies = ? WHERE id = ?",
                     (total, total - out, book_id))
        log(conn, "stock", f"Stock for “{book['title']}” set to {total} cop{'y' if total == 1 else 'ies'}", actor)
    return total - out


def delete_book(conn, book_id, actor):
    """Hard-delete a book that was never issued; archive one that has history so records stay intact."""
    with transaction(conn):
        book = conn.execute("SELECT title FROM books WHERE id = ? AND is_archived = 0", (book_id,)).fetchone()
        if book is None:
            raise LibraryError("That book no longer exists.", 404)
        if _active_count(conn, book_id):
            raise LibraryError(f"“{book['title']}” is currently issued. Collect all copies before deleting it.", 409)
        if conn.execute("SELECT 1 FROM issue_records WHERE book_id = ?", (book_id,)).fetchone():
            conn.execute("UPDATE books SET is_archived = 1 WHERE id = ?", (book_id,))
            result = "archived"
        else:
            conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
            result = "deleted"
        log(conn, "book_removed", f"“{book['title']}” was removed from the catalogue", actor)
    return result, book["title"]


# --------------------------------------------------------------------------- admin: categories
def add_category(conn, name, short, actor):
    name, short = " ".join((name or "").split()), " ".join((short or "").split()).upper()
    if not 2 <= len(name) <= 60:
        raise LibraryError("Category name must be 2–60 characters.", field="name")
    if not short:
        short = "".join(w[0] for w in name.split())[:6].upper() or name[:4].upper()
    if len(short) > 8:
        raise LibraryError("Short code must be 8 characters or fewer.", field="short_name")
    hue = sum(ord(c) for c in name) * 37 % 360
    try:
        with transaction(conn):
            conn.execute("INSERT INTO categories(name, short_name, hue) VALUES (?,?,?)", (name, short, hue))
            log(conn, "category", f"Category “{name}” was created", actor)
    except sqlite3.IntegrityError:
        raise LibraryError("A category with that name already exists.", 409, "name")


def rename_category(conn, cat_id, name, short, actor):
    name, short = " ".join((name or "").split()), " ".join((short or "").split()).upper()
    if not 2 <= len(name) <= 60 or not 1 <= len(short) <= 8:
        raise LibraryError("Use a name of 2–60 characters and a short code of 1–8 characters.")
    try:
        with transaction(conn):
            if conn.execute("UPDATE categories SET name=?, short_name=? WHERE id=?", (name, short, cat_id)).rowcount != 1:
                raise LibraryError("That category no longer exists.", 404)
            log(conn, "category", f"Category renamed to “{name}”", actor)
    except sqlite3.IntegrityError:
        raise LibraryError("A category with that name already exists.", 409)


def delete_category(conn, cat_id, actor):
    with transaction(conn):
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if cat is None:
            raise LibraryError("That category no longer exists.", 404)
        n = conn.execute("SELECT COUNT(*) FROM books WHERE category_id = ?", (cat_id,)).fetchone()[0]
        if n:
            raise LibraryError(f"“{cat['name']}” still has {n} book{'s' if n != 1 else ''}. "
                               "Move or delete them first.", 409)
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        log(conn, "category", f"Category “{cat['name']}” was deleted", actor)
    return cat["name"]
