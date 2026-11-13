from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..helpers import (today, arg_date, f_date, f_int, f_money, f_str, get_or_404_scoped, save_image, scoped,
                       staff_required, visible_loads)
from ..models import (CATEGORY_LABELS, DRIVER_CATEGORIES, EXPENSE_CATEGORIES, Expense, Load, Truck, User,
                      ROLE_DRIVER)
from .main import month_bounds

bp = Blueprint("expenses", __name__, url_prefix="/expenses")


def _categories():
    if current_user.is_driver:
        return [(k, v) for k, v in EXPENSE_CATEGORIES if k in DRIVER_CATEGORIES]
    return EXPENSE_CATEGORIES


def _visible():
    q = scoped(Expense)
    if current_user.is_driver:
        q = q.filter(Expense.user_id == current_user.id)
    return q


def _can_modify(e):
    if current_user.is_staff:
        return True
    return e.user_id == current_user.id and not e.approved and e.spent_on >= today() - timedelta(days=14)


@bp.route("/")
@login_required
def index():
    m_start, m_end = month_bounds(today())
    start = arg_date("start", m_start)
    end = arg_date("end", m_end - timedelta(days=1))
    cat = request.args.get("category") or ""
    who = request.args.get("user", type=int)
    q = _visible().filter(Expense.spent_on >= start, Expense.spent_on <= end)
    if cat in CATEGORY_LABELS:
        q = q.filter(Expense.category == cat)
    if who and current_user.is_staff:
        q = q.filter(Expense.user_id == who)
    if request.args.get("pending") == "1":
        q = q.filter(Expense.approved.is_(False))
    expenses = q.order_by(Expense.spent_on.desc(), Expense.id.desc()).all()
    by_cat = {}
    for e in expenses:
        by_cat[e.category_label] = by_cat.get(e.category_label, Decimal(0)) + e.amount
    pending = (scoped(Expense).filter(Expense.approved.is_(False)).count() if current_user.is_staff else 0)
    people = scoped(User).order_by(User.name).all() if current_user.is_staff else []
    return render_template("expenses/index.html", expenses=expenses, start=start, end=end, cat=cat, who=who,
                           total=sum((e.amount for e in expenses), Decimal(0)),
                           by_cat=sorted(by_cat.items(), key=lambda kv: -kv[1]),
                           categories=EXPENSE_CATEGORIES, people=people, pending=pending)


def _fill(e):
    e.spent_on = f_date("spent_on", default=today())
    cat = request.form.get("category")
    allowed = dict(_categories())
    e.category = cat if cat in allowed else "other"
    e.amount = f_money("amount") or Decimal(0)
    e.vendor = f_str("vendor", 120)
    e.gallons = f_money("gallons") if e.category == "fuel" else None
    e.odometer = f_int("odometer")
    e.notes = f_str("notes", 255)
    trk = f_int("truck_id")
    e.truck_id = get_or_404_scoped(Truck, trk).id if trk else None
    ld = f_int("load_id")
    if ld:
        load = visible_loads().filter(Load.id == ld).first() or abort(404)
        e.load_id = load.id
    else:
        e.load_id = None
    if current_user.is_staff:
        uid = f_int("user_id")
        e.user_id = get_or_404_scoped(User, uid).id if uid else current_user.id
    photo = request.files.get("receipt")
    if photo and photo.filename:
        name = save_image(photo, "receipt", load_id=e.load_id)
        if name:
            e.receipt_file = name
        else:
            flash("The receipt file wasn't an image, so it was skipped.", "warning")


def _form_ctx(e):
    loads = (visible_loads().filter(Load.status.in_(("assigned", "picked_up", "delivered")))
             .order_by(Load.number.desc()).limit(40).all())
    trucks = scoped(Truck).filter_by(is_active=True).order_by(Truck.unit).all()
    people = scoped(User).order_by(User.name).all() if current_user.is_staff else []
    return dict(e=e, categories=_categories(), loads=loads, trucks=trucks, people=people)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    e = Expense(company_id=current_user.company_id, user_id=current_user.id, spent_on=today(),
                category=request.args.get("category", "fuel"), load_id=request.args.get("load", type=int))
    if request.method == "POST":
        _fill(e)
        if e.amount <= 0:
            flash("Enter an amount.", "warning")
            return render_template("expenses/form.html", **_form_ctx(e))
        e.approved = current_user.is_staff
        db.session.add(e)
        db.session.commit()
        flash(f"Expense saved: ${e.amount:,.2f} {e.category_label}.", "success")
        if request.form.get("another"):
            return redirect(url_for("expenses.new", category=e.category))
        return redirect(url_for("main.dashboard") if current_user.is_driver else url_for("expenses.index"))
    if current_user.is_driver and not e.truck_id:
        last = (scoped(Expense).filter(Expense.user_id == current_user.id, Expense.truck_id.isnot(None))
                .order_by(Expense.id.desc()).first())
        e.truck_id = last.truck_id if last else None
    return render_template("expenses/form.html", **_form_ctx(e))


@bp.route("/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit(expense_id):
    e = _visible().filter(Expense.id == expense_id).first() or abort(404)
    if not _can_modify(e):
        abort(403)
    if request.method == "POST":
        _fill(e)
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("expenses.index"))
    return render_template("expenses/form.html", **_form_ctx(e))


@bp.route("/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete(expense_id):
    e = _visible().filter(Expense.id == expense_id).first() or abort(404)
    if not _can_modify(e):
        abort(403)
    db.session.delete(e)
    db.session.commit()
    flash("Expense deleted.", "success")
    return redirect(url_for("expenses.index"))


@bp.route("/<int:expense_id>/approve", methods=["POST"])
@staff_required
def approve(expense_id):
    e = get_or_404_scoped(Expense, expense_id)
    e.approved = not e.approved
    db.session.commit()
    return redirect(url_for("expenses.index", pending=1 if request.form.get("from_pending") else None))
