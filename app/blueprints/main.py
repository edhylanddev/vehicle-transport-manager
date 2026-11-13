import os
from datetime import date
from decimal import Decimal

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, send_from_directory,
                   url_for)
from flask_login import current_user, login_required

from ..extensions import db
from ..helpers import today, nulls_last, scoped, visible_loads
from ..models import Expense, Load, Truck, Upload, User, ROLE_DRIVER

bp = Blueprint("main", __name__)


@bp.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("main/landing.html")


@bp.route("/app")
def app_download():
    cfg = current_app.config
    apk_path = os.path.join(current_app.static_folder, "downloads", cfg["ANDROID_APK_FILE"])
    available = os.path.exists(apk_path)
    size_mb = round(os.path.getsize(apk_path) / 1024 / 1024, 1) if available else None
    return render_template("main/app_download.html", available=available, size_mb=size_mb,
                           version=cfg["ANDROID_VERSION_NAME"])


@bp.route("/app/version.json")
def app_version():
    """Polled by the Android app on launch to offer updates."""
    cfg = current_app.config
    return jsonify(versionCode=cfg["ANDROID_VERSION_CODE"], versionName=cfg["ANDROID_VERSION_NAME"],
                   url=url_for("static", filename="downloads/" + cfg["ANDROID_APK_FILE"], _external=True))


@bp.route("/manifest.webmanifest")
def manifest():
    resp = jsonify({
        "name": current_app.config["APP_NAME"],
        "short_name": "VTM",
        "start_url": "/dashboard",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#0f3d6e",
        "icons": [
            {"src": url_for("static", filename="img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="img/icon-512.png"), "sizes": "512x512", "type": "image/png"},
        ],
    })
    resp.mimetype = "application/manifest+json"
    return resp


@bp.route("/files/<path:name>")
@login_required
def uploaded_file(name):
    up = Upload.query.filter_by(name=name, company_id=current_user.company_id).first()
    if up is None:
        abort(404)
    # drivers may only open files attached to their own loads (or that they uploaded)
    if current_user.is_driver and name.startswith("dispatch-"):
        abort(404)
    if current_user.is_driver and up.uploaded_by_id != current_user.id:
        if up.load_id is None or not visible_loads().filter(Load.id == up.load_id).first():
            abort(404)
    resp = send_from_directory(current_app.config["UPLOAD_FOLDER"], name, max_age=86400)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


def month_bounds(d):
    start = d.replace(day=1)
    end = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    return start, end


@bp.route("/dashboard")
@login_required
def dashboard():
    if current_user.is_driver:
        active = (visible_loads().filter(Load.status.in_(("assigned", "picked_up")))
                  .order_by(*nulls_last(Load.pickup_date)).all())
        recent = (visible_loads().filter(Load.status.in_(("delivered", "paid")))
                  .order_by(*nulls_last(Load.delivered_at, desc=True)).limit(5).all())
        start, end = month_bounds(today())
        my_exp = (scoped(Expense).filter(Expense.user_id == current_user.id,
                                         Expense.spent_on >= start, Expense.spent_on < end).all())
        delivered_month = [l for l in visible_loads().filter(Load.status.in_(("delivered", "paid"))).all()
                           if start <= l.revenue_date < end]
        return render_template("main/dashboard_driver.html", active=active, recent=recent,
                               month_expenses=sum((e.amount for e in my_exp), Decimal(0)),
                               month_loads=len(delivered_month),
                               month_pay=sum((l.driver_pay or 0 for l in delivered_month), Decimal(0)))

    start, end = month_bounds(today())
    loads = scoped(Load)
    counts = dict(db.session.query(Load.status, db.func.count(Load.id))
                  .filter(Load.company_id == current_user.company_id).group_by(Load.status).all())
    revenue_loads = loads.filter(Load.status.in_(("delivered", "paid"))).all()
    month_rev = sum((l.price for l in revenue_loads if start <= l.revenue_date < end), Decimal(0))
    month_exp = (db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0))
                 .filter(Expense.company_id == current_user.company_id,
                         Expense.spent_on >= start, Expense.spent_on < end).scalar())
    month_exp = Decimal(month_exp or 0)
    unpaid = sorted([l for l in revenue_loads if l.balance_due > 0], key=lambda l: l.revenue_date)
    receivable = sum((l.balance_due for l in unpaid), Decimal(0))
    needs_driver = (loads.filter(Load.status == "booked", Load.driver_id.is_(None))
                    .order_by(*nulls_last(Load.pickup_date)).limit(10).all())
    on_road = (loads.filter(Load.status.in_(("assigned", "picked_up")))
               .order_by(*nulls_last(Load.pickup_date)).limit(15).all())
    co = current_user.company
    setup = None
    if not counts:  # brand-new company: show a getting-started checklist
        setup = [
            ("Add your company details (MC/DOT, phone, address)", bool(co.mc_number or co.dot_number or co.address),
             url_for("settings.company")) if current_user.is_owner else None,
            ("Add a truck", scoped(Truck).count() > 0, url_for("trucks.new")),
            ("Add your drivers", scoped(User).filter_by(role=ROLE_DRIVER).count() > 0,
             url_for("team.new") if current_user.is_owner else url_for("team.index")),
            ("Create your first load — or import a Central Dispatch sheet", False, url_for("loads.new")),
        ]
        setup = [x for x in setup if x]
    return render_template("main/dashboard_staff.html", setup=setup, counts=counts, month_rev=month_rev,
                           month_exp=month_exp, month_profit=month_rev - month_exp,
                           receivable=receivable, unpaid=unpaid[:10], unpaid_count=len(unpaid),
                           needs_driver=needs_driver, on_road=on_road, month_name=start.strftime("%B"))
