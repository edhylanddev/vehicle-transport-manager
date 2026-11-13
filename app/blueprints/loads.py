import io
import os
import uuid
from decimal import Decimal

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from .. import central_dispatch
from ..extensions import db
from ..helpers import (today, f_date, f_float, f_int, f_money, f_str, get_load_or_404, get_or_404_scoped,
                       nulls_last, owner_required, scoped, staff_required, visible_loads)
from ..models import (LOAD_STATUSES, OPEN_STATUSES, PAYMENT_METHODS, PAYMENT_TERMS, STATUS_LABELS,
                      TERMS_LABELS, Company, Customer, Load, LoadEvent, Payment, Truck, Upload, User, Vehicle,
                      ROLE_DRIVER, utcnow)

bp = Blueprint("loads", __name__, url_prefix="/loads")

PER_PAGE = 30


def log(load, message, kind="note", lat=None, lng=None):
    load.events.append(LoadEvent(user_id=current_user.id, kind=kind, message=message[:500], lat=lat, lng=lng))


def set_status(load, status):
    """Apply a status change + its side effects. Returns True if changed."""
    if status not in STATUS_LABELS or status == load.status:
        return False
    now = utcnow()
    if status in ("picked_up", "delivered", "paid") and not load.picked_up_at:
        load.picked_up_at = now
    if status in ("delivered", "paid") and not load.delivered_at:
        load.delivered_at = now
    if status in ("quote", "booked", "assigned"):
        load.picked_up_at = None
        load.delivered_at = None
    elif status == "picked_up":
        load.delivered_at = None
    old = load.status_label
    load.status = status
    log(load, f"Status: {old} → {load.status_label}", kind="status")
    return True


def drivers():
    return scoped(User).filter_by(role=ROLE_DRIVER, is_active_flag=True).order_by(User.name).all()


def trucks():
    return scoped(Truck).filter_by(is_active=True).order_by(Truck.unit).all()


# ---------------------------------------------------------------- list

@bp.route("/")
@login_required
def index():
    status = request.args.get("status", "open")
    q = (request.args.get("q") or "").strip()
    driver_id = request.args.get("driver", type=int)
    page = request.args.get("page", 1, type=int)

    query = visible_loads()
    if status == "open":
        query = query.filter(Load.status.in_(OPEN_STATUSES))
    elif status == "unpaid":
        query = query.filter(Load.status == "delivered")
    elif status in STATUS_LABELS:
        query = query.filter(Load.status == status)
    if driver_id and current_user.is_staff:
        query = query.filter(Load.driver_id == driver_id)
    if q:
        like = f"%{q}%"
        conds = [Load.reference.ilike(like), Load.pickup_city.ilike(like), Load.delivery_city.ilike(like),
                 Load.pickup_name.ilike(like), Load.delivery_name.ilike(like),
                 Load.vehicles.any(or_(Vehicle.vin.ilike(like), Vehicle.make.ilike(like),
                                       Vehicle.model.ilike(like), Vehicle.lot_number.ilike(like))),
                 Load.customer.has(Customer.name.ilike(like))]
        if q.lstrip("#").isdigit():
            conds.append(Load.number == int(q.lstrip("#")))
        query = query.filter(or_(*conds))

    if status == "open":
        query = query.order_by(*nulls_last(Load.pickup_date), Load.number.desc())
    else:
        query = query.order_by(Load.number.desc())
    pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("loads/index.html", loads=pagination.items, pagination=pagination,
                           status=status, q=q, driver_id=driver_id,
                           drivers=drivers() if current_user.is_staff else [],
                           statuses=LOAD_STATUSES)


# ---------------------------------------------------------------- create / edit

def _fill_from_form(load):
    load.customer_id = None
    cust_id = f_int("customer_id")
    if cust_id:
        load.customer_id = get_or_404_scoped(Customer, cust_id).id
    new_customer = f_str("new_customer", 150)
    if new_customer and not cust_id:
        c = Customer(company_id=current_user.company_id, name=new_customer,
                     kind=f_str("new_customer_kind", 30) or "broker",
                     contact_name=f_str("new_customer_contact", 120), phone=f_str("new_customer_phone", 40),
                     address=f_str("new_customer_address", 255))
        db.session.add(c)
        db.session.flush()
        load.customer_id = c.id
    load.reference = f_str("reference", 60)
    for side in ("pickup", "delivery"):
        for field, ln in (("name", 150), ("address", 255), ("city", 80), ("state", 20), ("zip", 12),
                          ("contact", 120), ("phone", 40)):
            setattr(load, f"{side}_{field}", f_str(f"{side}_{field}", ln))
        setattr(load, f"{side}_date", f_date(f"{side}_date"))
    if load.pickup_state:
        load.pickup_state = load.pickup_state.upper()
    if load.delivery_state:
        load.delivery_state = load.delivery_state.upper()
    load.miles = f_int("miles")
    load.price = f_money("price") or Decimal("0")
    load.driver_pay = f_money("driver_pay")
    load.broker_fee = f_money("broker_fee")
    terms = request.form.get("payment_terms")
    load.payment_terms = terms if terms in TERMS_LABELS else "cod"
    load.dispatch_notes = f_str("dispatch_notes", 5000)
    load.internal_notes = f_str("internal_notes", 5000)

    drv = f_int("driver_id")
    load.driver_id = None
    if drv:
        d = get_or_404_scoped(User, drv)
        if d.role != ROLE_DRIVER:
            abort(400)
        load.driver_id = d.id
    trk = f_int("truck_id")
    load.truck_id = get_or_404_scoped(Truck, trk).id if trk else None

    # vehicles: parallel lists; rows with nothing filled in are dropped
    fields = ("year", "make", "model", "color", "vin", "lot_number")
    cols = {f: request.form.getlist(f"v_{f}") for f in fields}
    operable = request.form.getlist("v_operable")
    rows = max((len(v) for v in cols.values()), default=0)
    load.vehicles.clear()
    for i in range(rows):
        vals = {f: (cols[f][i].strip() if i < len(cols[f]) else "") for f in fields}
        if not any(vals.values()):
            continue
        load.vehicles.append(Vehicle(
            year=vals["year"][:4] or None, make=vals["make"][:40] or None, model=vals["model"][:60] or None,
            color=vals["color"][:30] or None, vin=(vals["vin"].upper().replace(" ", "")[:17] or None),
            lot_number=vals["lot_number"][:40] or None,
            operable=(operable[i] != "0") if i < len(operable) else True))


def _form_context(load):
    return dict(load=load, customers=scoped(Customer).filter_by(is_archived=False).order_by(Customer.name).all(),
                drivers=drivers(), trucks=trucks(), terms=PAYMENT_TERMS, statuses=LOAD_STATUSES)


@bp.route("/import", methods=["POST"])
@staff_required
def import_sheet():
    """Upload a Central Dispatch dispatch sheet PDF -> pre-filled New load form (nothing saved yet)."""
    fs = request.files.get("sheet")
    if not fs or not fs.filename:
        flash("Choose the dispatch sheet PDF first.", "warning")
        return redirect(url_for("loads.new"))
    raw = fs.read()
    if not raw.startswith(b"%PDF"):
        flash("That file isn't a PDF. Download the dispatch sheet from Central Dispatch as a PDF and try again.", "danger")
        return redirect(url_for("loads.new"))
    try:
        data = central_dispatch.parse(io.BytesIO(raw))
    except central_dispatch.NotADispatchSheet as e:
        flash(str(e), "danger")
        return redirect(url_for("loads.new"))

    # keep the original PDF; it gets attached to the load when it's created
    name = f"dispatch-{uuid.uuid4().hex}.pdf"
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(raw)
    db.session.add(Upload(company_id=current_user.company_id, name=name, uploaded_by_id=current_user.id))
    db.session.commit()

    load = Load(company_id=current_user.company_id, status="booked", reference=data["reference"],
                price=data["price"] or Decimal(0), payment_terms=data["payment_terms"] or "cod",
                dispatch_notes=data["notes"])
    for side in ("pickup", "delivery"):
        d = data[side]
        for f in ("name", "address", "city", "state", "zip", "contact", "phone", "date"):
            setattr(load, f"{side}_{f}", d.get(f))
    internal = [f"Imported from Central Dispatch (load {data['reference'] or '?'})."]
    if data["payment_text"]:
        internal.append("Payment terms on sheet: " + data["payment_text"])
    cust = data["customer"]
    if cust.get("mc"):
        internal.append(f"Broker MC: {cust['mc']}")
    load.internal_notes = "\n".join(internal)
    vehicles = [Vehicle(**{k: v.get(k) for k in ("year", "make", "model", "color", "vin", "lot_number", "operable")})
                for v in data["vehicles"]]

    prefill = {"dispatch_sheet": name, "vehicles": vehicles}
    existing = (scoped(Customer).filter(db.func.lower(Customer.name) == (cust.get("name") or "").lower()).first()
                if cust.get("name") else None)
    if existing:
        load.customer_id = existing.id
    elif cust.get("name"):
        prefill.update(new_customer=cust["name"], new_customer_contact=cust.get("contact_name"),
                       new_customer_phone=cust.get("phone"), new_customer_address=cust.get("address"))
    dup = (scoped(Load).filter(Load.reference == data["reference"]).first() if data["reference"] else None)
    if dup:
        flash(f"Heads up: load #{dup.number} already has reference {data['reference']}. "
              "This might be a duplicate.", "warning")
    missing = [lbl for lbl, ok in (("price", data["price"]), ("pickup city", load.pickup_city),
                                   ("delivery city", load.delivery_city), ("vehicles", vehicles)) if not ok]
    if missing:
        flash(f"Couldn't find on the sheet: {', '.join(missing)}. Please fill those in.", "warning")
    return render_template("loads/form.html", prefill=prefill, **_form_context(load))


@bp.route("/new", methods=["GET", "POST"])
@staff_required
def new():
    load = Load(company_id=current_user.company_id, status="booked", payment_terms="cod")
    if request.method == "POST":
        _fill_from_form(load)
        status = request.form.get("status")
        if status in ("quote", "booked"):
            load.status = status
        if load.driver_id and load.status == "booked":
            load.status = "assigned"
        company = db.session.query(Company).filter_by(id=current_user.company_id).with_for_update().one()
        load.number = company.next_load_number
        company.next_load_number += 1
        log(load, f"Load created ({load.status_label})", kind="status")
        db.session.add(load)
        db.session.flush()
        sheet = f_str("dispatch_sheet", 255)
        if sheet and sheet.startswith("dispatch-"):
            up = scoped(Upload).filter_by(name=sheet, load_id=None).first()
            if up:
                up.load_id = load.id
                log(load, "Imported from a Central Dispatch dispatch sheet", kind="edit")
        db.session.commit()
        flash(f"Load #{load.number} created.", "success")
        return redirect(url_for("loads.detail", load_id=load.id))
    # prefill from ?customer=
    cid = request.args.get("customer", type=int)
    if cid:
        load.customer_id = cid
    return render_template("loads/form.html", **_form_context(load))


@bp.route("/<int:load_id>/edit", methods=["GET", "POST"])
@staff_required
def edit(load_id):
    load = get_load_or_404(load_id)
    if request.method == "POST":
        before_driver = load.driver_id
        _fill_from_form(load)
        if load.driver_id != before_driver:
            name = load.driver.name if load.driver_id else "nobody"
            log(load, f"Driver set to {name}", kind="assign")
            if load.driver_id and load.status == "booked":
                set_status(load, "assigned")
            if not load.driver_id and load.status == "assigned":
                set_status(load, "booked")
        log(load, "Load details edited", kind="edit")
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("loads.detail", load_id=load.id))
    return render_template("loads/form.html", **_form_context(load))


@bp.route("/<int:load_id>/duplicate", methods=["POST"])
@staff_required
def duplicate(load_id):
    src = get_load_or_404(load_id)
    company = db.session.query(Company).filter_by(id=current_user.company_id).with_for_update().one()
    copy_fields = [c.name for c in Load.__table__.columns
                   if c.name.startswith(("pickup_", "delivery_")) and not c.name.endswith("_date")]
    load = Load(company_id=src.company_id, number=company.next_load_number, status="booked",
                customer_id=src.customer_id, miles=src.miles, price=src.price, driver_pay=src.driver_pay,
                payment_terms=src.payment_terms, broker_fee=src.broker_fee,
                **{f: getattr(src, f) for f in copy_fields})
    company.next_load_number += 1
    log(load, f"Copied from load #{src.number}", kind="status")
    db.session.add(load)
    db.session.commit()
    flash(f"Load #{load.number} created from #{src.number}. Add the vehicles and dates.", "success")
    return redirect(url_for("loads.edit", load_id=load.id))


@bp.route("/<int:load_id>/delete", methods=["POST"])
@owner_required
def delete(load_id):
    load = get_load_or_404(load_id)
    num = load.number
    # keep expenses (they're real money spent) and stored files; just unlink them
    for e in list(load.expenses):
        e.load_id = None
    Upload.query.filter_by(load_id=load.id).update({"load_id": None})
    db.session.flush()
    db.session.delete(load)
    db.session.commit()
    flash(f"Load #{num} deleted.", "success")
    return redirect(url_for("loads.index"))


# ---------------------------------------------------------------- detail & actions

@bp.route("/<int:load_id>")
@login_required
def detail(load_id):
    load = get_load_or_404(load_id)
    sheets = (Upload.query.filter(Upload.load_id == load.id, Upload.name.like("dispatch-%")).all()
              if current_user.is_staff else [])
    return render_template("loads/detail.html", load=load, statuses=LOAD_STATUSES, sheets=sheets,
                           drivers=drivers() if current_user.is_staff else [],
                           trucks=trucks() if current_user.is_staff else [],
                           methods=PAYMENT_METHODS)


@bp.route("/<int:load_id>/status", methods=["POST"])
@staff_required
def change_status(load_id):
    load = get_load_or_404(load_id)
    status = request.form.get("status")
    if status == "assigned" and not load.driver_id:
        flash("Assign a driver first.", "warning")
    elif set_status(load, status):
        db.session.commit()
        flash(f"Load #{load.number} is now {load.status_label}.", "success")
    return redirect(url_for("loads.detail", load_id=load.id))


@bp.route("/<int:load_id>/assign", methods=["POST"])
@staff_required
def assign(load_id):
    load = get_load_or_404(load_id)
    drv = f_int("driver_id")
    driver = get_or_404_scoped(User, drv) if drv else None
    if driver and driver.role != ROLE_DRIVER:
        abort(400)
    trk = f_int("truck_id")
    load.truck_id = get_or_404_scoped(Truck, trk).id if trk else None
    pay = f_money("driver_pay")
    if pay is not None:
        load.driver_pay = pay
    elif driver and driver.pay_rate_pct and not load.driver_pay:
        load.driver_pay = (load.price * driver.pay_rate_pct / 100).quantize(Decimal("0.01"))
    if (driver.id if driver else None) != load.driver_id:
        load.driver_id = driver.id if driver else None
        log(load, f"Driver set to {driver.name if driver else 'nobody'}", kind="assign")
    if driver and load.status in ("quote", "booked"):
        set_status(load, "assigned")
    elif not driver and load.status == "assigned":
        set_status(load, "booked")
    db.session.commit()
    flash("Assignment saved.", "success")
    return redirect(url_for("loads.detail", load_id=load.id))


@bp.route("/<int:load_id>/miles", methods=["POST"])
@login_required
def set_miles(load_id):
    """Staff or the assigned driver can record the trip's actual miles."""
    load = get_load_or_404(load_id)
    if not (current_user.is_staff or load.driver_id == current_user.id):
        abort(403)
    miles = f_int("miles")
    if miles is None or miles <= 0 or miles > 10000:
        flash("Enter the trip miles (a number above 0).", "warning")
    else:
        old = load.miles
        load.miles = miles
        log(load, f"Miles set to {miles:,}" + (f" (was {old:,})" if old else ""), kind="edit")
        db.session.commit()
        flash("Miles saved.", "success")
    return redirect(url_for("loads.detail", load_id=load.id) + "#job-cost")


@bp.route("/<int:load_id>/note", methods=["POST"])
@login_required
def add_note(load_id):
    load = get_load_or_404(load_id)
    msg = f_str("message", 500)
    if msg:
        log(load, msg, kind="note", lat=f_float("lat"), lng=f_float("lng"))
        db.session.commit()
    return redirect(url_for("loads.detail", load_id=load.id) + "#activity")


@bp.route("/<int:load_id>/payments", methods=["POST"])
@staff_required
def add_payment(load_id):
    load = get_load_or_404(load_id)
    amount = f_money("amount")
    if not amount or amount <= 0:
        flash("Enter a payment amount.", "warning")
        return redirect(url_for("loads.detail", load_id=load.id))
    method = request.form.get("method")
    p = Payment(amount=amount, paid_on=f_date("paid_on", default=None) or today(),
                method=method if method in PAYMENT_METHODS else "other",
                reference=f_str("reference", 60), recorded_by_id=current_user.id)
    load.payments.append(p)
    log(load, f"Payment received: ${amount:,.2f} ({p.method})", kind="payment")
    if load.balance_due <= 0 and load.status == "delivered":
        set_status(load, "paid")
    db.session.commit()
    flash("Payment recorded.", "success")
    return redirect(url_for("loads.detail", load_id=load.id))


@bp.route("/<int:load_id>/payments/<int:payment_id>/delete", methods=["POST"])
@staff_required
def delete_payment(load_id, payment_id):
    load = get_load_or_404(load_id)
    p = next((p for p in load.payments if p.id == payment_id), None)
    if p is None:
        abort(404)
    load.payments.remove(p)
    log(load, f"Payment removed: ${p.amount:,.2f}", kind="payment")
    if load.status == "paid" and load.balance_due > 0:
        set_status(load, "delivered")
    db.session.commit()
    flash("Payment removed.", "success")
    return redirect(url_for("loads.detail", load_id=load.id))


# ---------------------------------------------------------------- printables

@bp.route("/<int:load_id>/invoice")
@staff_required
def invoice(load_id):
    load = get_load_or_404(load_id)
    if not load.invoiced_at and request.args.get("mark") == "1":
        load.invoiced_at = utcnow()
        log(load, "Invoice generated", kind="edit")
        db.session.commit()
    return render_template("loads/invoice.html", load=load, company=current_user.company)


@bp.route("/<int:load_id>/bol")
@login_required
def bol(load_id):
    load = get_load_or_404(load_id)
    return render_template("loads/bol.html", load=load, company=current_user.company)
