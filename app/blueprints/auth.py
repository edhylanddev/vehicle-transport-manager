import re
import threading
import time
from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import Company, User, ROLE_OWNER, utcnow

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


# --------------------------------------------------------------- self-service sign-up

_signup_lock = threading.Lock()
_signups_by_ip = {}  # ip -> [timestamps]; per worker process, a light brake on scripted sign-ups
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ip_allowed(ip):
    now = time.time()
    with _signup_lock:
        recent = [t for t in _signups_by_ip.get(ip, []) if now - t < 86400]
        _signups_by_ip[ip] = recent
        return len(recent) < current_app.config["SIGNUPS_PER_IP_PER_DAY"]


def _ip_record(ip):
    with _signup_lock:
        _signups_by_ip.setdefault(ip, []).append(time.time())


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if not current_app.config["ALLOW_SIGNUP"]:
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = {k: (request.form.get(k) or "").strip() for k in ("company", "name", "email", "phone")}
    if request.method == "POST":
        form["email"] = form["email"].lower()
        pw = request.form.get("password") or ""
        errors = []
        if request.form.get("website"):  # honeypot: humans never see this field
            abort(400)
        if not form["company"]:
            errors.append("Enter your company name.")
        if not form["name"]:
            errors.append("Enter your name.")
        if not EMAIL_RE.match(form["email"]):
            errors.append("Enter a valid email address.")
        elif User.query.filter_by(email=form["email"]).first():
            errors.append("There's already an account with that email. Sign in instead.")
        if len(pw) < 8:
            errors.append("Password must be at least 8 characters.")
        elif pw != request.form.get("confirm_password"):
            errors.append("Passwords don't match.")
        ip = request.remote_addr or "?"
        recent = Company.query.filter(Company.created_at >= utcnow() - timedelta(hours=1)).count()
        if not errors and (not _ip_allowed(ip) or recent >= current_app.config["SIGNUPS_PER_HOUR"]):
            errors.append("Too many new accounts right now. Please try again later.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/signup.html", form=form), 400
        co = Company(name=form["company"][:150], phone=form["phone"][:40] or None, email=form["email"])
        db.session.add(co)
        db.session.flush()
        u = User(company_id=co.id, name=form["name"][:120], email=form["email"], phone=form["phone"][:40] or None,
                 role=ROLE_OWNER)
        u.set_password(pw)
        u.last_login_at = utcnow()
        db.session.add(u)
        db.session.commit()
        _ip_record(ip)
        current_app.logger.info("New company signed up: %s (%s)", co.name, u.email)
        login_user(u, remember=True)
        flash(f"Welcome, {u.name.split(' ')[0]}! Your account for {co.name} is ready.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("auth/signup.html", form=form)


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
