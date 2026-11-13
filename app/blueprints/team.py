import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..helpers import f_money, f_str, get_or_404_scoped, owner_required, scoped, staff_required
from ..models import ROLES, ROLE_OWNER, Load, User

bp = Blueprint("team", __name__, url_prefix="/team")


def temp_password():
    # readable: no 0/O/1/l
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


@bp.route("/")
@staff_required
def index():
    users = scoped(User).order_by(User.is_active_flag.desc(), User.role, User.name).all()
    active_loads = dict(db.session.query(Load.driver_id, db.func.count(Load.id))
                        .filter(Load.company_id == current_user.company_id,
                                Load.status.in_(("assigned", "picked_up")))
                        .group_by(Load.driver_id).all())
    return render_template("team/index.html", users=users, active_loads=active_loads, roles=ROLES)


def _fill(u, is_new):
    u.name = f_str("name", 120) or u.name
    email = (f_str("email", 150) or "").lower()
    if email and email != u.email:
        if User.query.filter(User.email == email, User.id != (u.id or 0)).first():
            return "That email is already used by another login."
        u.email = email
    if not u.email:
        return "Email is required."
    u.phone = f_str("phone", 40)
    role = request.form.get("role")
    if role in ROLES:
        # never let the last active owner demote themselves
        if u.role == ROLE_OWNER and role != ROLE_OWNER and not is_new:
            owners = scoped(User).filter_by(role=ROLE_OWNER, is_active_flag=True).count()
            if owners <= 1:
                return "You're the only owner — add another owner before changing this role."
        u.role = role
    u.pay_rate_pct = f_money("pay_rate_pct")
    return None


@bp.route("/new", methods=["GET", "POST"])
@owner_required
def new():
    u = User(company_id=current_user.company_id, role="driver")
    if request.method == "POST":
        err = _fill(u, True)
        if not u.name:
            err = err or "Name is required."
        if err:
            flash(err, "danger")
            return render_template("team/form.html", u=u, roles=ROLES)
        pw = temp_password()
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        return render_template("team/password.html", u=u, password=pw, created=True)
    return render_template("team/form.html", u=u, roles=ROLES)


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@owner_required
def edit(user_id):
    u = get_or_404_scoped(User, user_id)
    if request.method == "POST":
        err = _fill(u, False)
        if err:
            db.session.rollback()
            flash(err, "danger")
            return redirect(url_for("team.edit", user_id=u.id))
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("team.index"))
    return render_template("team/form.html", u=u, roles=ROLES)


@bp.route("/<int:user_id>/reset-password", methods=["POST"])
@owner_required
def reset_password(user_id):
    u = get_or_404_scoped(User, user_id)
    if u.id == current_user.id:
        abort(400)
    pw = temp_password()
    u.set_password(pw)
    u.failed_logins = 0
    u.locked_until = None
    db.session.commit()
    return render_template("team/password.html", u=u, password=pw, created=False)


@bp.route("/<int:user_id>/toggle-active", methods=["POST"])
@owner_required
def toggle_active(user_id):
    u = get_or_404_scoped(User, user_id)
    if u.id == current_user.id:
        flash("You can't deactivate yourself.", "warning")
        return redirect(url_for("team.index"))
    u.is_active_flag = not u.is_active_flag
    u.session_version += 1  # sign them out everywhere
    db.session.commit()
    flash(f"{u.name} {'reactivated' if u.is_active_flag else 'deactivated and signed out'}.", "success")
    return redirect(url_for("team.index"))
