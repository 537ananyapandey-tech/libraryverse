"""Session helpers: CSRF tokens and login guards."""
import hmac
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, jsonify, redirect, request, session, url_for


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def verify_csrf():
    """Reject state-changing requests that do not carry this session's token."""
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="Your session expired. Reload the page and try again.")


def safe_next(target, fallback):
    """Only follow same-site relative redirects after login."""
    if target and target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc:
        return target
    return fallback


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="Admin login required."), 401
            return redirect(url_for("admin.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def student_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("student_id"):
            return redirect(url_for("student.login"))
        return view(*args, **kwargs)
    return wrapped


def current_actor():
    if session.get("admin_id"):
        return f"Admin ({session.get('admin_name', 'admin')})"
    if session.get("student_id"):
        return f"Student ({session.get('student_no', '')})"
    return "Circulation desk"
