"""Database models.

Every business record carries company_id so the app can later be reopened
to multiple companies (the shelved SaaS mode) without a schema rewrite.
"""
from datetime import datetime, date, timezone
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


Money = db.Numeric(12, 2)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------- company / users

class Company(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    mc_number = db.Column(db.String(30))
    dot_number = db.Column(db.String(30))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(150))
    address = db.Column(db.String(255))
    invoice_terms = db.Column(db.String(255), default="Payment due upon delivery.")
    next_load_number = db.Column(db.Integer, default=1001, nullable=False)


ROLE_OWNER = "owner"
ROLE_OFFICE = "office"
ROLE_DRIVER = "driver"
ROLES = {
    ROLE_OWNER: "Owner",
    ROLE_OFFICE: "Office / Dispatch",
    ROLE_DRIVER: "Driver",
}


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True, index=True)
    phone = db.Column(db.String(40))
    role = db.Column(db.String(20), nullable=False, default=ROLE_DRIVER)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active_flag = db.Column("is_active", db.Boolean, default=True, nullable=False)
    pay_rate_pct = db.Column(db.Numeric(5, 2))  # optional default driver % of load
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    # bumping this logs the user out everywhere (password change / deactivate)
    session_version = db.Column(db.Integer, default=1, nullable=False)

    company = db.relationship("Company")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)
        self.session_version = (self.session_version or 0) + 1

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_active(self):
        return self.is_active_flag

    def get_id(self):
        return f"{self.id}:{self.session_version}"

    @property
    def is_driver(self):
        return self.role == ROLE_DRIVER

    @property
    def is_owner(self):
        return self.role == ROLE_OWNER

    @property
    def is_staff(self):
        """Owner or office: can see money and every load."""
        return self.role in (ROLE_OWNER, ROLE_OFFICE)

    @property
    def role_label(self):
        return ROLES.get(self.role, self.role)


# ---------------------------------------------------------------- customers / trucks

class Customer(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    kind = db.Column(db.String(30), default="broker")  # broker, dealer, auction, private
    contact_name = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(150))
    address = db.Column(db.String(255))
    notes = db.Column(db.Text)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)

    loads = db.relationship("Load", back_populates="customer", lazy="dynamic")


CUSTOMER_KINDS = ["broker", "dealer", "auction", "private", "other"]


class Truck(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    unit = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(150))
    vin = db.Column(db.String(30))
    plate = db.Column(db.String(20))
    capacity = db.Column(db.Integer)  # number of cars
    is_active = db.Column(db.Boolean, default=True, nullable=False)


# ---------------------------------------------------------------- loads

LOAD_STATUSES = [
    ("quote", "Quote"),
    ("booked", "Booked"),
    ("assigned", "Assigned"),
    ("picked_up", "Picked up"),
    ("delivered", "Delivered"),
    ("paid", "Paid"),
    ("cancelled", "Cancelled"),
]
STATUS_LABELS = dict(LOAD_STATUSES)
OPEN_STATUSES = ("quote", "booked", "assigned", "picked_up")
# statuses a driver may move a load into, from the given current status
DRIVER_TRANSITIONS = {"assigned": "picked_up", "picked_up": "delivered"}

PAYMENT_TERMS = [
    ("cod", "COD (cash/check on delivery)"),
    ("cop", "COP (on pickup)"),
    ("quick_pay", "Quick pay"),
    ("invoice", "Invoice / net terms"),
    ("prepaid", "Prepaid"),
]
TERMS_LABELS = dict(PAYMENT_TERMS)


class Load(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(20), default="booked", nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), index=True)
    reference = db.Column(db.String(60))  # broker's order / dispatch id

    pickup_name = db.Column(db.String(150))
    pickup_address = db.Column(db.String(255))
    pickup_city = db.Column(db.String(80))
    pickup_state = db.Column(db.String(20))
    pickup_zip = db.Column(db.String(12))
    pickup_contact = db.Column(db.String(120))
    pickup_phone = db.Column(db.String(40))
    pickup_date = db.Column(db.Date)

    delivery_name = db.Column(db.String(150))
    delivery_address = db.Column(db.String(255))
    delivery_city = db.Column(db.String(80))
    delivery_state = db.Column(db.String(20))
    delivery_zip = db.Column(db.String(12))
    delivery_contact = db.Column(db.String(120))
    delivery_phone = db.Column(db.String(40))
    delivery_date = db.Column(db.Date)

    miles = db.Column(db.Integer)
    price = db.Column(Money, default=0, nullable=False)
    driver_pay = db.Column(Money)
    payment_terms = db.Column(db.String(20), default="cod")
    broker_fee = db.Column(Money)

    driver_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    truck_id = db.Column(db.Integer, db.ForeignKey("truck.id"))

    dispatch_notes = db.Column(db.Text)  # visible to driver
    internal_notes = db.Column(db.Text)  # staff only

    picked_up_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    invoiced_at = db.Column(db.DateTime)

    customer = db.relationship("Customer", back_populates="loads")
    driver = db.relationship("User", foreign_keys=[driver_id])
    truck = db.relationship("Truck")
    vehicles = db.relationship("Vehicle", back_populates="load", cascade="all, delete-orphan",
                               order_by="Vehicle.id")
    payments = db.relationship("Payment", back_populates="load", cascade="all, delete-orphan",
                               order_by="Payment.paid_on")
    events = db.relationship("LoadEvent", back_populates="load", cascade="all, delete-orphan",
                             order_by="LoadEvent.created_at.desc()")
    inspections = db.relationship("Inspection", back_populates="load", cascade="all, delete-orphan",
                                  order_by="Inspection.created_at")
    expenses = db.relationship("Expense", back_populates="load",
                               order_by="Expense.spent_on")

    __table_args__ = (db.UniqueConstraint("company_id", "number", name="uq_load_company_number"),)

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def terms_label(self):
        return TERMS_LABELS.get(self.payment_terms, self.payment_terms or "")

    @property
    def amount_paid(self):
        return sum((p.amount for p in self.payments), Decimal("0"))

    @property
    def balance_due(self):
        if self.status == "cancelled":
            return Decimal("0")
        return (self.price or Decimal("0")) - self.amount_paid

    @property
    def route_short(self):
        a = ", ".join(x for x in (self.pickup_city, self.pickup_state) if x) or "?"
        b = ", ".join(x for x in (self.delivery_city, self.delivery_state) if x) or "?"
        return f"{a} → {b}"

    @property
    def vehicles_short(self):
        if not self.vehicles:
            return "No vehicles"
        first = self.vehicles[0].title
        more = len(self.vehicles) - 1
        return first + (f" +{more}" if more else "")

    def job_costs(self):
        """Actual cost of this job from the expenses logged against it.

        Driver pay entered on the load counts as a cost unless a driver_pay
        expense was also logged for this load (then only the logged one counts).
        """
        exps = list(self.expenses)
        fuel = [e for e in exps if e.category == "fuel"]
        fuel_cost = sum((e.amount for e in fuel), Decimal(0))
        gallons = sum((e.gallons or Decimal(0) for e in fuel), Decimal(0))
        logged_pay = sum((e.amount for e in exps if e.category == "driver_pay"), Decimal(0))
        other = sum((e.amount for e in exps if e.category not in ("fuel", "driver_pay")), Decimal(0))
        driver_pay = logged_pay if logged_pay else (self.driver_pay or Decimal(0))
        broker_fee = self.broker_fee or Decimal(0)
        total = fuel_cost + other + driver_pay + broker_fee
        revenue = self.price or Decimal(0)
        miles = self.miles or 0

        def per_mile(v):
            return (Decimal(v) / miles).quantize(Decimal("0.01")) if miles else None

        return {
            "expenses": exps, "fuel_stops": len(fuel), "fuel_cost": fuel_cost, "gallons": gallons,
            "avg_price_per_gallon": (fuel_cost / gallons).quantize(Decimal("0.001")) if gallons else None,
            "mpg": (Decimal(miles) / gallons).quantize(Decimal("0.1")) if gallons and miles else None,
            "other": other, "driver_pay": driver_pay, "broker_fee": broker_fee, "total": total,
            "revenue": revenue, "profit": revenue - total, "miles": miles,
            "fuel_per_mile": per_mile(fuel_cost), "cost_per_mile": per_mile(total),
            "revenue_per_mile": per_mile(revenue), "profit_per_mile": per_mile(revenue - total),
        }

    @property
    def revenue_date(self):
        """Date a load counts toward revenue (its delivery)."""
        if self.delivered_at:
            return self.delivered_at.date()
        return self.delivery_date or self.pickup_date or self.created_at.date()

    @property
    def is_revenue(self):
        return self.status in ("delivered", "paid")

    def inspection(self, kind):
        for i in self.inspections:
            if i.kind == kind:
                return i
        return None


class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    load_id = db.Column(db.Integer, db.ForeignKey("load.id"), nullable=False, index=True)
    year = db.Column(db.String(4))
    make = db.Column(db.String(40))
    model = db.Column(db.String(60))
    color = db.Column(db.String(30))
    vin = db.Column(db.String(17), index=True)
    lot_number = db.Column(db.String(40))
    operable = db.Column(db.Boolean, default=True, nullable=False)

    load = db.relationship("Load", back_populates="vehicles")

    @property
    def title(self):
        t = " ".join(x for x in (self.year, self.make, self.model) if x)
        return t or (f"VIN …{self.vin[-6:]}" if self.vin else "Vehicle")


class LoadEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    load_id = db.Column(db.Integer, db.ForeignKey("load.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(30), default="note")  # status, note, assign, payment, inspection
    message = db.Column(db.String(500), nullable=False)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    load = db.relationship("Load", back_populates="events")
    user = db.relationship("User")


PAYMENT_METHODS = ["check", "cash", "ach", "zelle", "card", "comchek", "other"]


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    load_id = db.Column(db.Integer, db.ForeignKey("load.id"), nullable=False, index=True)
    amount = db.Column(Money, nullable=False)
    paid_on = db.Column(db.Date, default=date.today, nullable=False)
    method = db.Column(db.String(20), default="check")
    reference = db.Column(db.String(60))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    load = db.relationship("Load", back_populates="payments")


# ---------------------------------------------------------------- inspections (condition reports)

class Inspection(db.Model):
    """Pickup or delivery condition report: photos, damage notes, signature."""
    id = db.Column(db.Integer, primary_key=True)
    load_id = db.Column(db.Integer, db.ForeignKey("load.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)  # pickup | delivery
    driver_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    damage_notes = db.Column(db.Text)
    odometer = db.Column(db.String(20))
    signer_name = db.Column(db.String(120))
    signature_file = db.Column(db.String(255))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    load = db.relationship("Load", back_populates="inspections")
    driver = db.relationship("User")
    photos = db.relationship("InspectionPhoto", cascade="all, delete-orphan",
                             order_by="InspectionPhoto.id")


class InspectionPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey("inspection.id"), nullable=False, index=True)
    file = db.Column(db.String(255), nullable=False)
    caption = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------- expenses

EXPENSE_CATEGORIES = [
    ("fuel", "Fuel"),
    ("tolls", "Tolls"),
    ("repairs", "Repairs & maintenance"),
    ("tires", "Tires"),
    ("insurance", "Insurance"),
    ("truck_payment", "Truck / trailer payment"),
    ("permits", "Permits & fees"),
    ("lodging", "Lodging"),
    ("meals", "Meals"),
    ("driver_pay", "Driver pay"),
    ("dispatch", "Dispatch / load board"),
    ("office", "Office & software"),
    ("other", "Other"),
]
CATEGORY_LABELS = dict(EXPENSE_CATEGORIES)
DRIVER_CATEGORIES = ("fuel", "tolls", "repairs", "tires", "lodging", "meals", "other")


class Expense(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    spent_on = db.Column(db.Date, default=date.today, nullable=False, index=True)
    category = db.Column(db.String(20), nullable=False, default="fuel")
    amount = db.Column(Money, nullable=False)
    vendor = db.Column(db.String(120))
    gallons = db.Column(db.Numeric(8, 3))
    odometer = db.Column(db.Integer)
    notes = db.Column(db.String(255))
    receipt_file = db.Column(db.String(255))
    truck_id = db.Column(db.Integer, db.ForeignKey("truck.id"))
    load_id = db.Column(db.Integer, db.ForeignKey("load.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # who logged it / driver
    approved = db.Column(db.Boolean, default=True, nullable=False)

    truck = db.relationship("Truck")
    load = db.relationship("Load", back_populates="expenses")
    user = db.relationship("User")

    @property
    def price_per_gallon(self):
        if self.category == "fuel" and self.gallons:
            return (self.amount / self.gallons).quantize(Decimal("0.001"))
        return None

    @property
    def category_label(self):
        return CATEGORY_LABELS.get(self.category, self.category)


# ---------------------------------------------------------------- uploads registry

class Upload(db.Model):
    """Every stored file, so /files/<name> can check the requester's company."""
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    load_id = db.Column(db.Integer, db.ForeignKey("load.id"), index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------- web push (iPhone / browser notifications)

class PushSubscription(db.Model):
    """One browser or home-screen app that asked for the Sunrise notification."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    endpoint = db.Column(db.String(700), nullable=False, unique=True)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(100), nullable=False)
    device = db.Column(db.String(60))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_sent_on = db.Column(db.Date)

    user = db.relationship("User")

    def info(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}
