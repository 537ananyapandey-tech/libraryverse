import sqlite3

from flask import (Blueprint, abort, flash, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from database import get_db
from services import library
from services.library import LibraryError
from services.security import admin_required, safe_next

bp = Blueprint("admin", __name__, url_prefix="/admin")

_DUMMY_HASH = "scrypt:32768:8:1$dummy$" + "0" * 128


def actor():
    return f"Admin ({session.get('admin_name', 'admin')})"


# ------------------------------------------------------------------ auth
@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_id"):
        return redirect(url_for("admin.dashboard"))
    nxt = request.values.get("next", "")
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        row = get_db().execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
        ok = bool(row) and check_password_hash(row["password_hash"], password)
        if ok:
            session.clear()
            session.update(admin_id=row["id"], admin_name=row["username"])
            session.permanent = True
            library.log(get_db(), "login", f"{row['username']} signed in to the admin panel", "Admin")
            flash("Signed in. Welcome to the admin panel.", "success")
            return redirect(safe_next(nxt, url_for("admin.dashboard")))
        flash("Username or password is incorrect.", "error")
        return render_template("admin/login.html", username=username, next=nxt), 401
    return render_template("admin/login.html", username="", next=nxt)


@bp.post("/logout")
def logout():
    session.clear()
    flash("Signed out of the admin panel.", "info")
    return redirect(url_for("public.index"))


# ------------------------------------------------------------------ overview
@bp.route("/")
@admin_required
def dashboard():
    db = get_db()
    overdue, _ = library.list_issues(db, status="overdue", limit=6)
    recent = library.recent_issues(db, 8)
    return render_template("admin/index.html", stats=library.stats(db), overdue=overdue,
                           pending=library.list_requests(db, status="PENDING"),
                           pending_returns=library.list_issues(db, status="pending_return", limit=100)[0],
                           recent=recent, activity=library.recent_activity(db, 6))


# ------------------------------------------------------------------ books
@bp.route("/books")
@admin_required
def books():
    db = get_db()
    page = int(request.args["page"]) if request.args.get("page", "").isdigit() else 1
    result = library.search_books(
        db, q=request.args.get("q", ""),
        category=request.args.get("category") if request.args.get("category", "").isdigit() else None,
        sort=request.args.get("sort", "title"), page=page, per_page=25)
    return render_template("admin/books.html", result=result, categories=library.categories(db), args=request.args)


def _book_form(book=None, form=None, errors=None):
    db = get_db()
    return render_template("admin/book_form.html", book=book, form=form or (book or {}),
                           errors=errors or {}, categories=library.categories(db))


@bp.route("/books/new", methods=["GET", "POST"])
@admin_required
def book_new():
    if request.method == "POST":
        clean, errors = library.validate_book_form(get_db(), request.form)
        if errors:
            flash("Please fix the highlighted fields.", "error")
            return _book_form(form=request.form, errors=errors), 422
        try:
            new_id = library.create_book(get_db(), clean, actor())
        except sqlite3.IntegrityError:
            flash("That ISBN is already in the catalogue.", "error")
            return _book_form(form=request.form, errors={"isbn": "ISBN already exists."}), 422
        flash(f"Added “{clean['title']}” to the catalogue.", "success")
        return redirect(url_for("public.book_detail", book_id=new_id))
    return _book_form(form={"total_copies": 3})


@bp.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
@admin_required
def book_edit(book_id):
    db = get_db()
    book = library.get_book(db, book_id)
    if book is None:
        abort(404)
    if request.method == "POST":
        clean, errors = library.validate_book_form(db, request.form, book_id)
        if errors:
            flash("Please fix the highlighted fields.", "error")
            return _book_form(book=book, form=request.form, errors=errors), 422
        try:
            library.update_book(db, book_id, clean, actor())
        except LibraryError as e:
            flash(e.message, "error")
            return _book_form(book=book, form=request.form, errors={e.field or "title": e.message}), e.status
        flash(f"Saved changes to “{clean['title']}”.", "success")
        return redirect(url_for("admin.books"))
    return _book_form(book=book)


@bp.post("/books/<int:book_id>/stock")
@admin_required
def book_stock(book_id):
    try:
        left = library.set_stock(get_db(), book_id, request.form.get("total_copies"), actor())
        flash(f"Stock updated. {left} cop{'y' if left == 1 else 'ies'} available now.", "success")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(request.referrer or url_for("admin.books"))


@bp.post("/books/<int:book_id>/delete")
@admin_required
def book_delete(book_id):
    try:
        result, title = library.delete_book(get_db(), book_id, actor())
        flash(f"“{title}” was {'deleted' if result == 'deleted' else 'removed from the catalogue (its issue history is kept)'}.",
              "success")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(url_for("admin.books"))


# ------------------------------------------------------------------ circulation + people
@bp.route("/issues")
@admin_required
def issues():
    status = request.args.get("status", "all")
    if status not in {"all", "active", "overdue", "returned", "pending_return"}:
        status = "all"
    rows, total = library.list_issues(get_db(), q=request.args.get("q", ""), status=status, limit=200)
    return render_template("admin/issues.html", issues=rows, total=total, status=status, q=request.args.get("q", ""))


@bp.post("/requests/<int:request_id>/<decision>")
@admin_required
def request_decide(request_id, decision):
    if decision not in {"approve", "reject"}:
        abort(404)
    try:
        if decision == "approve":
            r = library.approve_request(get_db(), request_id, actor())
            flash(f"Approved. “{r['book_title']}” issued to {r['student_name']}, due {r['due_date']}.", "success")
        else:
            r = library.reject_request(get_db(), request_id, actor())
            flash(f"Rejected the request by {r['student_name']} for “{r['book_title']}”.", "info")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(url_for("admin.dashboard") + "#requests")


@bp.post("/returns/<int:issue_id>/approve")
@admin_required
def return_approve(issue_id):
    try:
        r = library.approve_return(get_db(), issue_id, actor())
        fine = f" Fine ₹{r['fine']} recorded as paid." if r["fine"] else ""
        flash(f"Approved. “{r['book_title']}” returned by {r['student_name']}.{fine}", "success")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(url_for("admin.dashboard") + "#returns")


@bp.route("/students")
@admin_required
def students():
    return render_template("admin/students.html", students=library.list_students(get_db()))


# ------------------------------------------------------------------ categories
@bp.route("/categories")
@admin_required
def categories():
    return render_template("admin/categories.html", categories=library.category_distribution(get_db()))


@bp.post("/categories/add")
@admin_required
def category_add():
    try:
        library.add_category(get_db(), request.form.get("name"), request.form.get("short_name"), actor())
        flash("Category created.", "success")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(url_for("admin.categories"))


@bp.post("/categories/<int:cat_id>/rename")
@admin_required
def category_rename(cat_id):
    try:
        library.rename_category(get_db(), cat_id, request.form.get("name"), request.form.get("short_name"), actor())
        flash("Category updated.", "success")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(url_for("admin.categories"))


@bp.post("/categories/<int:cat_id>/delete")
@admin_required
def category_delete(cat_id):
    try:
        name = library.delete_category(get_db(), cat_id, actor())
        flash(f"Category “{name}” deleted.", "success")
    except LibraryError as e:
        flash(e.message, "error")
    return redirect(url_for("admin.categories"))


# ------------------------------------------------------------------ reports
@bp.route("/reports")
@admin_required
def reports():
    db = get_db()
    overdue, _ = library.list_issues(db, status="overdue", limit=50)
    dist = library.category_distribution(db)
    return render_template(
        "reports.html", stats=library.stats(db), most_issued=library.most_issued(db, 10),
        distribution=dist, max_copies=max([c["copies"] for c in dist] or [1]) or 1,
        overdue=overdue, activity=library.recent_activity(db, 12))
