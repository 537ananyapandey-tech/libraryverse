from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import config
from database import get_db
from services import library
from services.library import LibraryError
from services.security import current_actor, student_required

bp = Blueprint("student", __name__, url_prefix="/student")

# A real hash so unknown IDs take as long to reject as wrong passwords.
_DUMMY_HASH = "scrypt:32768:8:1$dummy$" + "0" * 128


@bp.route("/", methods=["GET"])
def home():
    if not session.get("student_id"):
        return redirect(url_for("student.login"))
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (session["student_id"],)).fetchone()
    if student is None:
        session.pop("student_id", None)
        return redirect(url_for("student.login"))
    rows, _ = library.list_issues(db, status="all", limit=100, student_id=student["id"])
    return render_template(
        "student_home.html", student=student,
        current=[r for r in rows if r["status"] != "returned"],
        returned=[r for r in rows if r["status"] == "returned"],
        overdue=[r for r in rows if r["status"] == "overdue"],
        loan_days=config.LOAN_DAYS,
        requests=library.list_requests(db, student_id=student["id"], limit=50),
    )


@bp.post("/request/<int:book_id>")
@student_required
def request_book(book_id):
    try:
        rec = library.request_book(get_db(), session["student_id"], book_id)
        flash(f"Request sent for “{rec['book_title']}”. A librarian will review it soon.", "success")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(url_for("student.home"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        number = (request.form.get("enrollment_no") or "").strip()
        password = request.form.get("password") or ""
        student = library.get_student(get_db(), number)
        ok = check_password_hash(student["password_hash"] if student else _DUMMY_HASH, password) if student else False
        if ok:
            session.clear()
            session.update(student_id=student["id"], student_no=student["enrollment_no"],
                           student_name=student["name"])
            session.permanent = True
            flash(f"Welcome back, {student['name'].split()[0]}.", "success")
            return redirect(url_for("student.home"))
        flash("Student ID or password is incorrect.", "error")
        return render_template("student_login.html", enrollment_no=number), 401
    return render_template("student_login.html", enrollment_no="")


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("public.index"))
