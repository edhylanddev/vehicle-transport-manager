from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..extensions import db
from ..helpers import f_str, get_or_404_scoped, scoped, staff_required
from ..models import CUSTOMER_KINDS, Customer, Load

bp = Blueprint("customers", __name__, url_prefix="/customers")


@bp.route("/")
@staff_required
def index():
    q = (request.args.get("q") or "").strip()
    show_archived = request.args.get("archived") == "1"
    query = scoped(Customer).filter_by(is_archived=show_archived)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Customer.name.ilike(like), Customer.contact_name.ilike(like),
                                 Customer.phone.ilike(like), Customer.email.ilike(like)))
    customers = query.order_by(Customer.name).all()
    # loads count + open balance per customer, in one pass
    stats = {}
    for l in scoped(Load).filter(Load.customer_id.in_([c.id for c in customers] or [0])).all():
        s = stats.setdefault(l.customer_id, {"loads": 0, "balance": Decimal(0)})
        s["loads"] += 1
        if l.is_revenue:
            s["balance"] += l.balance_due
    return render_template("customers/index.html", customers=customers, stats=stats, q=q,
                           show_archived=show_archived)


def _fill(c):
    c.name = f_str("name", 150) or c.name or "Unnamed"
    kind = request.form.get("kind")
    c.kind = kind if kind in CUSTOMER_KINDS else "other"
    c.contact_name = f_str("contact_name", 120)
    c.phone = f_str("phone", 40)
    c.email = f_str("email", 150)
    c.address = f_str("address", 255)
    c.notes = f_str("notes", 5000)


@bp.route("/new", methods=["GET", "POST"])
@staff_required
def new():
    c = Customer(company_id=current_user.company_id, kind="broker")
    if request.method == "POST":
        _fill(c)
        db.session.add(c)
        db.session.commit()
        flash("Customer added.", "success")
        return redirect(url_for("customers.detail", customer_id=c.id))
    return render_template("customers/form.html", c=c, kinds=CUSTOMER_KINDS)


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@staff_required
def edit(customer_id):
    c = get_or_404_scoped(Customer, customer_id)
    if request.method == "POST":
        _fill(c)
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("customers.detail", customer_id=c.id))
    return render_template("customers/form.html", c=c, kinds=CUSTOMER_KINDS)


@bp.route("/<int:customer_id>")
@staff_required
def detail(customer_id):
    c = get_or_404_scoped(Customer, customer_id)
    loads = c.loads.order_by(Load.number.desc()).all()
    revenue = [l for l in loads if l.is_revenue]
    totals = {"loads": len(loads),
              "billed": sum((l.price for l in revenue), Decimal(0)),
              "balance": sum((l.balance_due for l in revenue), Decimal(0))}
    return render_template("customers/detail.html", c=c, loads=loads[:100], totals=totals)


@bp.route("/<int:customer_id>/archive", methods=["POST"])
@staff_required
def archive(customer_id):
    c = get_or_404_scoped(Customer, customer_id)
    c.is_archived = not c.is_archived
    db.session.commit()
    flash("Customer archived." if c.is_archived else "Customer restored.", "success")
    return redirect(url_for("customers.detail", customer_id=c.id))
