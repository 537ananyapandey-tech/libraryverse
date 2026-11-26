from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

import config
from database import get_db
from services import library

bp = Blueprint("public", __name__)


@bp.route("/")
def index():
    db = get_db()
    featured = library.featured_books(db, 6)
    return render_template(
        "index.html",
        stats=library.stats(db),
        featured=featured,
        shelf=featured[:5],
        categories=library.category_distribution(db)[:8],
        recent=library.recent_books(db, 8),
    )


@bp.route("/dashboard")
def dashboard():
    db = get_db()
    dist = library.category_distribution(db)
    return render_template(
        "dashboard.html",
        stats=library.stats(db),
        recent_issues=library.recent_issues(db, 5),
        recent_books=library.recent_books(db, 5),
        popular=library.popular_books(db, 5),
        activity=library.recent_activity(db, 8),
        distribution=dist,
        max_titles=max([c["titles"] for c in dist] or [1]) or 1,
    )


@bp.route("/books")
def books():
    db = get_db()
    return render_template(
        "books.html",
        categories=library.categories(db),
        subjects=library.subjects(db),
        args=request.args,
    )


@bp.route("/books/<int:book_id>")
def book_detail(book_id):
    db = get_db()
    book = library.get_book(db, book_id)
    if book is None:
        abort(404)
    my_loan = my_pending = False
    if session.get("student_id"):
        sid = session["student_id"]
        my_loan = db.execute("SELECT 1 FROM issue_records WHERE student_id=? AND book_id=? AND returned_at IS NULL",
                             (sid, book_id)).fetchone() is not None
        my_pending = db.execute("SELECT 1 FROM issue_requests WHERE student_id=? AND book_id=? AND status='PENDING'",
                                (sid, book_id)).fetchone() is not None
    return render_template("book_details.html", book=book, related=library.related_books(db, book),
                           my_loan=my_loan, my_pending=my_pending)


@bp.route("/issue")
def issue_page():
    if not session.get("admin_id"):
        # Direct issuing is a librarian-desk action. Students request books instead.
        if session.get("student_id"):
            flash("Books are issued after a librarian approves your request. Pick a book and tap Request this book.", "info")
            target = url_for("public.book_detail", book_id=int(request.args["book"])) if request.args.get("book", "").isdigit() \
                else url_for("public.books")
            return redirect(target)
        return redirect(url_for("admin.login", next=request.full_path.rstrip("?")))
    db = get_db()
    book = None
    if request.args.get("book", "").isdigit():
        book = library.get_book(db, int(request.args["book"]))
    today = library.today()
    return render_template(
        "issue.html",
        book=book,
        today=today.isoformat(),
        due=(today + timedelta(days=config.LOAN_DAYS)).isoformat(),
        min_date=(today - timedelta(days=config.MAX_BACKDATE_DAYS)).isoformat(),
        prefill_no=session.get("student_no", ""),
        prefill_name=session.get("student_name", ""),
    )


@bp.route("/return")
def return_page():
    return render_template("return.html", q=request.args.get("q", ""), fine_per_day=config.FINE_PER_DAY)
