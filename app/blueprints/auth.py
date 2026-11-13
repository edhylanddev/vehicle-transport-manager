from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User, utcnow

bp = Blueprint("auth", __name__)


def _safe_next(target):
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            flash("Too many failed attempts. Try again in a few minutes.", "danger")
        elif user and user.is_active and user.check_password(password):
            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = now
            db.session.commit()
            login_user(user, remember=True)
            return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))
        else:
            if user:
                user.failed_logins = (user.failed_logins or 0) + 1
                if user.failed_logins >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
                    user.locked_until = now + timedelta(minutes=current_app.config["LOGIN_LOCK_MINUTES"])
                    user.failed_logins = 0
                db.session.commit()
            flash("Wrong email or password.", "danger")
    return render_template("auth/login.html", email=email)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            current_user.name = (request.form.get("name") or current_user.name).strip()[:120]
            current_user.phone = (request.form.get("phone") or "").strip()[:40] or None
            db.session.commit()
            flash("Profile saved.", "success")
        elif action == "password":
            cur = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            if not current_user.check_password(cur):
                flash("Current password is wrong.", "danger")
            elif len(new) < 8:
                flash("New password must be at least 8 characters.", "danger")
            elif new != request.form.get("confirm_password"):
                flash("New passwords don't match.", "danger")
            else:
                current_user.set_password(new)
                db.session.commit()
                login_user(current_user, remember=True)  # refresh token w/ new session_version
                flash("Password changed. Other devices have been signed out.", "success")
        return redirect(url_for("auth.account"))
    return render_template("auth/account.html")
