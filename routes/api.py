from flask import Blueprint, jsonify, render_template, request

import config
from database import get_db
from services import library
from services.security import admin_required, current_actor

bp = Blueprint("api", __name__, url_prefix="/api")


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@bp.get("/books")
def books():
    a = request.args
    result = library.search_books(
        get_db(), q=a.get("q", ""), category=_int(a.get("category"), None) or None,
        subject=a.get("subject", ""), availability=a.get("availability", ""),
        sort=a.get("sort", "title"), page=_int(a.get("page"), 1),
        per_page=_int(a.get("per_page"), config.PER_PAGE))
    html = render_template("_book_cards.html", books=result["items"])
    return jsonify(ok=True, html=html, total=result["total"], page=result["page"],
                   pages=result["pages"], has_more=result["has_more"])


@bp.get("/book-options")
def book_options():
    return jsonify(ok=True, items=library.book_options(get_db(), request.args.get("q", "")))


@bp.get("/students/<path:enrollment_no>")
def student_lookup(enrollment_no):
    s = library.get_student(get_db(), enrollment_no)
    if s is None:
        return jsonify(ok=True, found=False)
    return jsonify(ok=True, found=True, name=s["name"], enrollment_no=s["enrollment_no"])


@bp.post("/issue")
@admin_required
def issue():
    data = request.get_json(silent=True) or {}
    rec = library.issue_book(
        get_db(), data.get("student_no"), data.get("student_name"), data.get("book_id"),
        data.get("issue_date"), data.get("due_date"), actor=current_actor())
    note = " The student was registered automatically." if rec["new_student"] else ""
    return jsonify(ok=True, record=rec, message=f"Issued “{rec['book_title']}” to {rec['student_name']}.{note}"), 201


@bp.get("/issues")
def issues():
    rows, total = library.list_issues(
        get_db(), q=request.args.get("q", ""), status=request.args.get("status", "active"),
        limit=min(_int(request.args.get("limit"), 30), 100))
    return jsonify(ok=True, total=total, html=render_template("_issue_rows.html", issues=rows))


@bp.post("/return/<int:issue_id>")
def return_book(issue_id):
    """Request a return. The loan stays open (Pending Admin Approval) until an admin approves the bill."""
    rec = library.request_return(get_db(), issue_id)
    bill = f" Fine: ₹{rec['fine']} ({rec['days_late']} day{'s' if rec['days_late'] != 1 else ''} late at ₹{rec['fine_per_day']}/day)." \
        if rec["fine"] else " No fine."
    return jsonify(ok=True, record=rec, message=f"Return requested for “{rec['book_title']}”.{bill} Pending admin approval.")
