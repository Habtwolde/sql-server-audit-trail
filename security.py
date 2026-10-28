"""Application authentication. SQL Server connection credentials are separate."""
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta

from flask import abort, request, session
from werkzeug.security import check_password_hash
from storage import connect

_attempts = {}
_lock = threading.Lock()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def configure_security(app, mode):
    secret = os.environ.get("FLASK_SECRET_KEY", "")
    if mode == "live" and len(secret) < 32:
        raise RuntimeError("Set FLASK_SECRET_KEY to at least 32 random characters in .env")
    app.secret_key = secret or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=65536)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_request():
        if mode == "live" and session.get("user"):
            with connect() as db:
                user = db.execute("SELECT role FROM app_users WHERE username=?", (session["user"],)).fetchone()
            if not user:
                session.clear()
            else:
                session["role"] = user["role"]
        if request.method == "POST":
            token = request.form.get("csrf_token", "")
            if not token or not hmac.compare_digest(token, session.get("csrf", "")):
                abort(400, "Session expired or invalid request. Reload the page and try again.")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Cache-Control"] = "no-store"
        return response


def password_login():
    key = request.remote_addr
    now = time.monotonic()
    with _lock:
        recent = [t for t in _attempts.get(key, []) if now - t < 300]
        if len(recent) >= 10:
            return "Too many attempts. Try again in five minutes."
        _attempts[key] = recent + [now]
    username = request.form.get("username", "").strip().lower()
    with connect() as db:
        user = db.execute("SELECT * FROM app_users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], request.form.get("password", "")):
        return "Invalid username or password."
    with _lock:
        _attempts.pop(key, None)
    session.clear()
    session.permanent = True
    session["user"], session["role"] = username, user["role"]
    return False


def csv_cell(value):
    """Make exported text safe for spreadsheet opening; raw SQL remains in the DB."""
    if value is None:
        return ""
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value
