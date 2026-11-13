from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..helpers import f_int, f_str, owner_required
from ..models import Load

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.route("/", methods=["GET", "POST"])
@owner_required
def company():
    co = current_user.company
    if request.method == "POST":
        co.name = f_str("name", 150) or co.name
        co.mc_number = f_str("mc_number", 30)
        co.dot_number = f_str("dot_number", 30)
        co.phone = f_str("phone", 40)
        co.email = f_str("email", 150)
        co.address = f_str("address", 255)
        co.invoice_terms = f_str("invoice_terms", 255)
        nxt = f_int("next_load_number")
        if nxt and nxt != co.next_load_number:
            highest = (db.session.query(db.func.max(Load.number))
                       .filter(Load.company_id == co.id).scalar() or 0)
            if nxt > highest:
                co.next_load_number = nxt
            else:
                flash(f"Next load number must be above #{highest}.", "warning")
        db.session.commit()
        flash("Company settings saved.", "success")
        return redirect(url_for("settings.company"))
    return render_template("settings/company.html", co=co)
