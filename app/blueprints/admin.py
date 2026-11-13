"""Platform admin: every company that has signed up. Only for SUPERADMIN_EMAILS."""
from flask import Blueprint, abort, current_app, render_template
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Company, Expense, Load, User

bp = Blueprint("admin", __name__, url_prefix="/admin")


def is_superadmin(user):
    return bool(user and user.is_authenticated and user.email.lower() in current_app.config["SUPERADMIN_EMAILS"])


@bp.route("/")
@login_required
def companies():
    if not is_superadmin(current_user):
        abort(404)

    def per_company(q, col):
        return dict(q.with_entities(col, db.func.count()).group_by(col).all())

    loads = per_company(Load.query, Load.company_id)
    users = per_company(User.query, User.company_id)
    expenses = per_company(Expense.query, Expense.company_id)
    last_login = dict(db.session.query(User.company_id, db.func.max(User.last_login_at)).group_by(User.company_id).all())
    last_load = dict(db.session.query(Load.company_id, db.func.max(Load.created_at)).group_by(Load.company_id).all())
    owners = {}
    for u in User.query.filter_by(role="owner").order_by(User.id).all():
        owners.setdefault(u.company_id, u)
    rows = [{"co": c, "owner": owners.get(c.id), "loads": loads.get(c.id, 0), "users": users.get(c.id, 0),
             "expenses": expenses.get(c.id, 0), "last_login": last_login.get(c.id), "last_load": last_load.get(c.id)}
            for c in Company.query.order_by(Company.created_at.desc()).all()]
    return render_template("admin/companies.html", rows=rows)
