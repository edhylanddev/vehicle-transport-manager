from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..helpers import f_int, f_str, get_or_404_scoped, scoped, staff_required
from ..models import Truck

bp = Blueprint("trucks", __name__, url_prefix="/trucks")


@bp.route("/")
@staff_required
def index():
    trucks = scoped(Truck).order_by(Truck.is_active.desc(), Truck.unit).all()
    return render_template("trucks/index.html", trucks=trucks)


def _fill(t):
    t.unit = f_str("unit", 40) or t.unit or "Truck"
    t.description = f_str("description", 150)
    t.vin = (f_str("vin", 30) or "").upper() or None
    t.plate = f_str("plate", 20)
    t.capacity = f_int("capacity")
    t.is_active = request.form.get("is_active", "1") == "1"


@bp.route("/new", methods=["GET", "POST"])
@staff_required
def new():
    t = Truck(company_id=current_user.company_id, is_active=True)
    if request.method == "POST":
        _fill(t)
        db.session.add(t)
        db.session.commit()
        flash("Truck added.", "success")
        return redirect(url_for("trucks.index"))
    return render_template("trucks/form.html", t=t)


@bp.route("/<int:truck_id>/edit", methods=["GET", "POST"])
@staff_required
def edit(truck_id):
    t = get_or_404_scoped(Truck, truck_id)
    if request.method == "POST":
        _fill(t)
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("trucks.index"))
    return render_template("trucks/form.html", t=t)
