import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, Response, render_template, request
from flask_login import current_user

from ..helpers import today, arg_date, scoped, staff_required
from ..models import Expense, Load, Truck, User
from .main import month_bounds

bp = Blueprint("reports", __name__, url_prefix="/reports")

D0 = Decimal(0)


def presets():
    t = today()
    m_start, m_end = month_bounds(t)
    last_end = m_start - timedelta(days=1)
    last_start = last_end.replace(day=1)
    return [
        ("This month", m_start, m_end - timedelta(days=1)),
        ("Last month", last_start, last_end),
        ("Last 30 days", t - timedelta(days=29), t),
        ("Year to date", t.replace(month=1, day=1), t),
        ("Last year", date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)),
    ]


def _range():
    m_start, m_end = month_bounds(today())
    start = arg_date("start", m_start)
    end = arg_date("end", m_end - timedelta(days=1))
    if end < start:
        start, end = end, start
    return start, end


def revenue_loads(start, end):
    loads = scoped(Load).filter(Load.status.in_(("delivered", "paid"))).all()
    return [l for l in loads if start <= l.revenue_date <= end]


def expenses_in(start, end):
    return (scoped(Expense).filter(Expense.spent_on >= start, Expense.spent_on <= end)
            .order_by(Expense.spent_on).all())


def ratio(a, b):
    return (Decimal(a) / Decimal(b)).quantize(Decimal("0.01")) if b else None


@bp.route("/")
@staff_required
def index():
    start, end = _range()
    loads = revenue_loads(start, end)
    exps = expenses_in(start, end)

    revenue = sum((l.price for l in loads), D0)
    driver_pay = sum((l.driver_pay or D0 for l in loads), D0)
    broker_fees = sum((l.broker_fee or D0 for l in loads), D0)
    expense_total = sum((e.amount for e in exps), D0)
    # driver pay recorded on loads counts as a cost unless it was ALSO logged as a driver_pay expense
    logged_driver_pay = sum((e.amount for e in exps if e.category == "driver_pay"), D0)
    total_cost = expense_total + broker_fees + (driver_pay if not logged_driver_pay else D0)
    miles = sum((l.miles or 0 for l in loads))
    cars = sum((len(l.vehicles) for l in loads))

    by_cat = {}
    for e in exps:
        by_cat[e.category_label] = by_cat.get(e.category_label, D0) + e.amount
    if driver_pay and not logged_driver_pay:
        by_cat["Driver pay (from loads)"] = driver_pay
    if broker_fees:
        by_cat["Broker / dispatch fees (from loads)"] = broker_fees

    users = {u.id: u for u in scoped(User).all()}
    by_driver = {}
    for l in loads:
        key = l.driver_id or 0
        r = by_driver.setdefault(key, {"name": users[key].name if key in users else "Unassigned",
                                       "loads": 0, "cars": 0, "revenue": D0, "pay": D0, "miles": 0, "expenses": D0})
        r["loads"] += 1
        r["cars"] += len(l.vehicles)
        r["revenue"] += l.price
        r["pay"] += l.driver_pay or D0
        r["miles"] += l.miles or 0
    for e in exps:
        if e.user_id in users and users[e.user_id].is_driver:
            r = by_driver.setdefault(e.user_id, {"name": users[e.user_id].name, "loads": 0, "cars": 0,
                                                 "revenue": D0, "pay": D0, "miles": 0, "expenses": D0})
            r["expenses"] += e.amount

    trucks = {t.id: t for t in scoped(Truck).all()}
    by_truck = {}
    for l in loads:
        key = l.truck_id or 0
        r = by_truck.setdefault(key, {"name": trucks[key].unit if key in trucks else "No truck",
                                      "loads": 0, "revenue": D0, "expenses": D0, "miles": 0})
        r["loads"] += 1
        r["revenue"] += l.price
        r["miles"] += l.miles or 0
    for e in exps:
        if e.truck_id:
            r = by_truck.setdefault(e.truck_id, {"name": trucks[e.truck_id].unit if e.truck_id in trucks else "?",
                                                 "loads": 0, "revenue": D0, "expenses": D0, "miles": 0})
            r["expenses"] += e.amount

    by_customer = {}
    for l in loads:
        name = l.customer.name if l.customer else "(none)"
        r = by_customer.setdefault(name, {"loads": 0, "revenue": D0, "balance": D0})
        r["loads"] += 1
        r["revenue"] += l.price
        r["balance"] += l.balance_due

    # 12-month trend ending at `end`
    trend = []
    m = end.replace(day=1)
    months = []
    for _ in range(12):
        months.append(m)
        m = (m - timedelta(days=1)).replace(day=1)
    months.reverse()
    t_start = months[0]
    all_rev = revenue_loads(t_start, end)
    all_exp = expenses_in(t_start, end)
    for ms in months:
        me = month_bounds(ms)[1]
        trend.append({
            "label": ms.strftime("%b %y"),
            "revenue": sum((l.price for l in all_rev if ms <= l.revenue_date < me), D0),
            "expenses": sum((e.amount for e in all_exp if ms <= e.spent_on < me), D0),
        })
    trend_max = max([max(t["revenue"], t["expenses"]) for t in trend] + [D0]) or Decimal(1)

    summary = {
        "revenue": revenue, "loads": len(loads), "cars": cars, "miles": miles,
        "expenses": total_cost, "profit": revenue - total_cost,
        "rev_per_mile": ratio(revenue, miles), "cost_per_mile": ratio(total_cost, miles),
        "rev_per_car": ratio(revenue, cars),
        "fuel_gallons": sum((e.gallons or D0 for e in exps if e.category == "fuel"), D0),
    }
    return render_template("reports/index.html", start=start, end=end, presets=presets(), s=summary,
                           by_cat=sorted(by_cat.items(), key=lambda kv: -kv[1]),
                           by_driver=sorted(by_driver.values(), key=lambda r: -r["revenue"]),
                           by_truck=sorted(by_truck.values(), key=lambda r: -r["revenue"]),
                           by_customer=sorted(by_customer.items(), key=lambda kv: -kv[1]["revenue"]),
                           trend=trend, trend_max=trend_max)


def _csv(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/loads.csv")
@staff_required
def loads_csv():
    start, end = _range()
    q = (scoped(Load).filter(Load.pickup_date >= start, Load.pickup_date <= end)
         .order_by(Load.number).all())
    rows = [(l.number, l.status_label, l.reference, l.customer.name if l.customer else "",
             l.pickup_date, l.pickup_city, l.pickup_state, l.delivery_date, l.delivery_city, l.delivery_state,
             len(l.vehicles), "; ".join(v.title + (f" {v.vin}" if v.vin else "") for v in l.vehicles),
             l.miles, l.price, l.driver_pay, l.broker_fee, l.terms_label, l.amount_paid, l.balance_due,
             l.driver.name if l.driver else "", l.truck.unit if l.truck else "")
            for l in q]
    return _csv(f"loads_{start}_{end}.csv",
                ["Load #", "Status", "Reference", "Customer", "Pickup date", "Pickup city", "Pickup state",
                 "Delivery date", "Delivery city", "Delivery state", "Cars", "Vehicles", "Miles", "Price",
                 "Driver pay", "Broker fee", "Terms", "Paid", "Balance", "Driver", "Truck"], rows)


@bp.route("/expenses.csv")
@staff_required
def expenses_csv():
    start, end = _range()
    rows = [(e.spent_on, e.category_label, e.amount, e.vendor, e.gallons, e.odometer,
             e.truck.unit if e.truck else "", e.load.number if e.load else "", e.user.name if e.user else "",
             e.notes, "yes" if e.receipt_file else "")
            for e in expenses_in(start, end)]
    return _csv(f"expenses_{start}_{end}.csv",
                ["Date", "Category", "Amount", "Vendor", "Gallons", "Odometer", "Truck", "Load #", "Logged by",
                 "Notes", "Receipt"], rows)
