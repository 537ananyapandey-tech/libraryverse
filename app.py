"""LIBRARYVERSE entry point.  Run with:  python app.py"""
import os
import secrets
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, render_template, request, session

import config
import database
from services import security
from services.library import LibraryError


def _load_secret_key(instance_path):
    """Persist a random secret so sessions survive restarts, without committing a key to code."""
    path = os.path.join(instance_path, "secret.key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
            if key:
                return key
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


def create_app(test_config=None):
    app = Flask(__name__)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        DATABASE=os.path.join(app.instance_path, "library.db"),
        SECRET_KEY=_load_secret_key(app.instance_path),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    database.init_db(app.config["DATABASE"])
    app.teardown_appcontext(database.close_db)

    from routes import admin, api, public, student
    for module in (public, api, student, admin):
        app.register_blueprint(module.bp)

    # ------------------------------------------------------------ template helpers
    @app.template_filter("fmt_date")
    def fmt_date(value):
        if not value:
            return ""
        try:
            d = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
        return f"{d.day} {d.strftime('%b %Y')}"

    @app.template_filter("fmt_when")
    def fmt_when(value):
        try:
            dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return str(value)
        delta = datetime.now() - dt
        if delta.days <= 0:
            return dt.strftime("Today, %I:%M %p").replace(" 0", " ")
        if delta.days == 1:
            return "Yesterday"
        if delta.days < 7:
            return f"{delta.days} days ago"
        return f"{dt.day} {dt.strftime('%b')}"

    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": security.csrf_token,
            "is_admin": bool(session.get("admin_id")),
            "is_student": bool(session.get("student_id")),
            "student_first": (session.get("student_name") or "").split(" ")[0],
            "LOAN_DAYS": config.LOAN_DAYS,
        }

    # ------------------------------------------------------------ request hooks
    @app.before_request
    def csrf_protect():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            security.verify_csrf()

    @app.after_request
    def secure_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith(("/api/", "/admin", "/student")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ errors
    def wants_json():
        return request.path.startswith("/api/")

    @app.errorhandler(LibraryError)
    def handle_library_error(err):
        if wants_json():
            return jsonify(ok=False, error=err.message, field=err.field), err.status
        return render_template("error.html", code=err.status, title="That didn't work", message=err.message), err.status

    def make_http_handler(code, title, default_message):
        def handler(err):
            message = getattr(err, "description", None) or default_message
            if code == 404:
                message = default_message
            if wants_json():
                return jsonify(ok=False, error=message), code
            return render_template("error.html", code=code, title=title, message=message), code
        return handler

    app.register_error_handler(400, make_http_handler(400, "Request not accepted", "That request could not be processed."))
    app.register_error_handler(404, make_http_handler(404, "This shelf is empty", "We couldn't find that page or book."))
    app.register_error_handler(405, make_http_handler(405, "Wrong turn", "That action isn't available here."))
    app.register_error_handler(413, make_http_handler(413, "Too much data", "That request was too large."))
    app.register_error_handler(500, make_http_handler(500, "Something broke on our side",
                                                     "An unexpected error occurred. Your data is safe; try again."))
    return app


if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("LIBRARYVERSE_HOST", "127.0.0.1")
    port = int(os.environ.get("LIBRARYVERSE_PORT", "5000"))
    print(f"\n  LIBRARYVERSE is running at http://{host}:{port}\n  Press Ctrl+C to stop.\n")
    app.run(host=host, port=port, debug=False)
