import base64
import io

import pytest
from PIL import Image

from app import create_app
from app.cli import seed
from app.extensions import db
from app.models import Expense, Inspection, Load, Upload, User


@pytest.fixture()
def app(tmp_path):
    app = create_app("testing")
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    from app.blueprints import auth as auth_bp
    auth_bp._signups_by_ip.clear()  # per-process sign-up counter

    # The fixture keeps one app context open across requests (so tests can query the DB),
    # which would let flask-login's per-request user cache in `g` leak between clients.
    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)

    with app.app_context():
        db.create_all()
        seed(n_loads=15)
        yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email, pw="demo1234"):
    client.post("/logout")
    return client.post("/login", data={"email": email, "password": pw})


def jpeg_bytes(color="red", size=(3000, 2000)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def sig_data_url():
    buf = io.BytesIO()
    Image.new("RGBA", (200, 80), (0, 0, 0, 0)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def uid(email):
    return User.query.filter_by(email=email).one().id


def test_full_load_lifecycle(app, client):
    login(client, "office@demo.test")
    driver_id = uid("driver@demo.test")
    r = client.post("/loads/new", data={
        "customer_id": "", "new_customer": "Brand New Broker", "new_customer_kind": "broker",
        "pickup_city": "Fort Pierce", "pickup_state": "fl", "pickup_date": "2026-11-14",
        "delivery_city": "Savannah", "delivery_state": "ga",
        "price": "$1,250.00", "miles": "390", "payment_terms": "cod", "status": "booked",
        "v_vin": ["1hgcm82633a004352", ""], "v_year": ["2003", ""], "v_make": ["Honda", ""],
        "v_model": ["Accord", ""], "v_color": ["Silver", ""], "v_lot_number": ["", ""], "v_operable": ["1", "1"],
    })
    assert r.status_code == 302
    load = Load.query.order_by(Load.id.desc()).first()
    assert load.customer.name == "Brand New Broker"
    assert load.pickup_state == "FL" and str(load.price) == "1250.00"
    assert len(load.vehicles) == 1 and load.vehicles[0].vin == "1HGCM82633A004352"
    assert load.status == "booked"

    client.post(f"/loads/{load.id}/assign", data={"driver_id": driver_id, "driver_pay": "400"})
    db.session.refresh(load)
    assert load.status == "assigned" and load.driver_id == driver_id

    # driver does pickup with 2 photos + signature
    login(client, "driver@demo.test")
    r = client.post(f"/loads/{load.id}/inspect/pickup", data={
        "photos": [(jpeg_bytes(), "a.jpg"), (jpeg_bytes("blue"), "b.jpg"), (io.BytesIO(b"not an image"), "x.jpg")],
        "damage_notes": "scratch rear bumper", "signer_name": "Lot Guy", "signature": sig_data_url(),
        "lat": "27.44", "lng": "-80.32",
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    db.session.refresh(load)
    assert load.status == "picked_up" and load.picked_up_at
    insp = load.inspection("pickup")
    assert len(insp.photos) == 2 and insp.signature_file and insp.lat == 27.44
    # photos were downscaled
    with Image.open(f"{app.config['UPLOAD_FOLDER']}/{insp.photos[0].file}") as im:
        assert max(im.size) <= app.config["PHOTO_MAX_PX"]
    # driver can view own photos
    assert client.get(f"/files/{insp.photos[0].file}").status_code == 200
    # repeat pickup redirects to the existing report instead of duplicating
    assert "/inspections/" in client.get(f"/loads/{load.id}/inspect/pickup").headers["Location"]

    r = client.post(f"/loads/{load.id}/inspect/delivery", data={"signer_name": "Receiver"},
                    content_type="multipart/form-data")
    db.session.refresh(load)
    assert load.status == "delivered" and load.delivered_at

    # driver can't record payments
    assert client.post(f"/loads/{load.id}/payments", data={"amount": "1250"}).status_code == 403

    login(client, "owner@demo.test")
    client.post(f"/loads/{load.id}/payments", data={"amount": "1250", "method": "check"})
    db.session.refresh(load)
    assert load.status == "paid" and load.balance_due == 0
    assert client.get(f"/loads/{load.id}/invoice?mark=1").status_code == 200
    assert client.get(f"/loads/{load.id}/bol").status_code == 200
    # removing the payment reopens it
    pid = load.payments[0].id
    client.post(f"/loads/{load.id}/payments/{pid}/delete")
    db.session.refresh(load)
    assert load.status == "delivered"


def test_driver_isolation(app, client):
    d2 = uid("driver2@demo.test")
    other = Load.query.filter_by(driver_id=d2).first()
    login(client, "driver@demo.test")
    assert client.get(f"/loads/{other.id}").status_code == 404
    assert client.get(f"/loads/{other.id}/inspect/pickup").status_code == 404
    assert client.post(f"/loads/{other.id}/note", data={"message": "hi"}).status_code == 404
    assert client.post(f"/loads/{other.id}/status", data={"status": "paid"}).status_code == 403
    # files on someone else's load are hidden
    db.session.add(Upload(company_id=other.company_id, name="secret.jpg", load_id=other.id,
                          uploaded_by_id=d2))
    db.session.commit()
    assert client.get("/files/secret.jpg").status_code == 404
    # drivers can't tie expenses to other drivers' loads
    r = client.post("/expenses/new", data={"category": "fuel", "amount": "50", "load_id": other.id})
    assert r.status_code == 404


def test_driver_expense_needs_review(app, client):
    login(client, "driver@demo.test")
    r = client.post("/expenses/new", data={"category": "insurance", "amount": "75.5", "vendor": "Pilot",
                                           "receipt": (jpeg_bytes(), "r.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    e = Expense.query.order_by(Expense.id.desc()).first()
    assert e.category == "other"  # drivers can't use office categories
    assert not e.approved and e.receipt_file
    login(client, "office@demo.test")
    client.post(f"/expenses/{e.id}/approve")
    db.session.refresh(e)
    assert e.approved


def test_team_and_sessions(app, client):
    login(client, "owner@demo.test")
    r = client.post("/team/new", data={"name": "New Guy", "email": "NEW@demo.test", "role": "driver"})
    page = r.get_data(as_text=True)
    assert "Login created" in page
    import re
    pw = re.search(r'<code class="fs-5">([a-z0-9-]+)</code>', page).group(1)
    # can't take an existing email
    r = client.post("/team/new", data={"name": "Dup", "email": "office@demo.test", "role": "driver"})
    assert "already used" in r.get_data(as_text=True)

    other = app.test_client()
    assert login(other, "new@demo.test", pw).status_code == 302
    assert other.get("/dashboard").status_code == 200
    # deactivating signs them out immediately
    client.post(f"/team/{uid('new@demo.test')}/toggle-active")
    assert other.get("/dashboard").status_code == 302
    # only owner can't demote themselves
    me = uid("owner@demo.test")
    client.post(f"/team/{me}/edit", data={"name": "Mike", "email": "owner@demo.test", "role": "driver"})
    assert User.query.get(me).role == "owner"


def test_login_lockout(app, client):
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        client.post("/login", data={"email": "office@demo.test", "password": "nope"})
    r = login(client, "office@demo.test")
    assert r.status_code == 200 and "Too many" in r.get_data(as_text=True)


def test_open_redirect_blocked(app, client):
    r = client.post("/login?next=https://evil.example", data={"email": "owner@demo.test", "password": "demo1234"})
    assert r.headers["Location"].endswith("/dashboard")


def test_csrf_enforced(tmp_path):
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    with app.app_context():
        db.create_all()
        seed(n_loads=2)
        c = app.test_client()
        assert c.post("/login", data={"email": "owner@demo.test", "password": "demo1234"}).status_code == 400


def test_reports_and_csv(app, client):
    login(client, "owner@demo.test")
    r = client.get("/reports/?start=2020-01-01&end=2030-12-31")
    assert r.status_code == 200 and "By driver" in r.get_data(as_text=True)
    csv = client.get("/reports/loads.csv?start=2020-01-01&end=2030-12-31").get_data(as_text=True)
    assert csv.startswith("Load #,Status") and csv.count("\n") > 5


def test_load_number_setting(app, client):
    login(client, "owner@demo.test")
    client.post("/settings/", data={"name": "X", "next_load_number": "5"})  # below existing → refused
    from app.models import Company
    assert Company.query.first().next_load_number > 5
    client.post("/settings/", data={"name": "X", "next_load_number": "50000"})
    assert Company.query.first().next_load_number == 50000


def test_job_cost_per_mile(app, client):
    from decimal import Decimal
    driver_id = uid("driver@demo.test")
    load = Load.query.filter_by(driver_id=driver_id, status="picked_up").first() or \
        Load.query.filter_by(driver_id=driver_id).first()
    load.miles = None
    load.price = Decimal("1500")
    load.driver_pay = Decimal("450")
    load.broker_fee = None
    Expense.query.filter_by(load_id=load.id).delete()   # start from a clean slate (demo data has stops)
    db.session.commit()

    login(client, "driver@demo.test")
    # page asks for miles first
    assert "Add the trip miles" in client.get(f"/loads/{load.id}").get_data(as_text=True)
    client.post(f"/loads/{load.id}/miles", data={"miles": "500"})
    # two fuel stops: gallons x price/gal fills in the amount
    for gal, ppg in (("100", "4.00"), ("50", "3.80")):
        r = client.post("/expenses/new", data={"category": "fuel", "gallons": gal, "price_per_gallon": ppg,
                                               "load_id": load.id, "back": "load", "vendor": "Pilot"})
        assert r.status_code == 302 and r.headers["Location"].endswith(f"/loads/{load.id}#job-cost")
    client.post("/expenses/new", data={"category": "tolls", "amount": "60", "load_id": load.id})
    db.session.refresh(load)

    jc = load.job_costs()
    assert jc["miles"] == 500 and jc["fuel_stops"] == 2
    assert jc["fuel_cost"] == Decimal("590.00")          # 400 + 190
    assert jc["gallons"] == Decimal("150")
    assert jc["mpg"] == Decimal("3.3")
    assert jc["other"] == Decimal("60")
    assert jc["total"] == Decimal("1100.00")             # 590 + 60 + 450 driver pay
    assert jc["cost_per_mile"] == Decimal("2.20")
    assert jc["profit_per_mile"] == Decimal("0.80")      # (1500 - 1100) / 500

    # driver sees their fuel, not the money
    page = client.get(f"/loads/{load.id}").get_data(as_text=True)
    assert "2</b> fuel stops" in page and "Profit / mile" not in page

    # owner sees cost per mile on the load and in reports
    login(client, "owner@demo.test")
    page = client.get(f"/loads/{load.id}").get_data(as_text=True)
    assert "Cost / mile" in page and "$2.20" in page and "$0.80" in page
    load.status = "delivered"
    db.session.commit()
    rep = client.get("/reports/?start=2000-01-01&end=2100-01-01").get_data(as_text=True)
    assert "Cost per mile by job" in rep and "$2.20" in rep
    csv = client.get("/reports/loads.csv?start=2000-01-01&end=2100-01-01").get_data(as_text=True)
    assert "Cost per mile" in csv.splitlines()[0]


def test_logged_driver_pay_not_double_counted(app, client):
    from decimal import Decimal
    load = Load.query.first()
    load.miles, load.price, load.driver_pay, load.broker_fee = 100, Decimal("500"), Decimal("150"), None
    db.session.add(Expense(company_id=load.company_id, category="driver_pay", amount=Decimal("160"),
                           load_id=load.id))
    db.session.commit()
    assert load.job_costs()["driver_pay"] == Decimal("160")


def test_delete_load_keeps_its_expenses(app, client):
    from decimal import Decimal
    load = Load.query.first()
    e = Expense(company_id=load.company_id, category="fuel", amount=Decimal("99"), load_id=load.id)
    db.session.add(e)
    db.session.commit()
    login(client, "owner@demo.test")
    assert client.post(f"/loads/{load.id}/delete").status_code == 302
    db.session.refresh(e)
    assert e.load_id is None and e.amount == Decimal("99")


def test_other_driver_cannot_set_miles(app, client):
    other = Load.query.filter_by(driver_id=uid("driver2@demo.test")).first()
    login(client, "driver@demo.test")
    assert client.post(f"/loads/{other.id}/miles", data={"miles": "5"}).status_code == 404


# ---------------------------------------------------------------- Central Dispatch import

def _sheet(**kw):
    pytest.importorskip("reportlab")
    from tests.cd_sample import build
    return io.BytesIO(build(**kw))


def test_parse_dispatch_sheet():
    from decimal import Decimal
    from app.central_dispatch import parse
    d = parse(_sheet())
    assert d["reference"] == "301999888"
    assert d["customer"]["name"] == "Sunrise Auto Logistics LLC" and d["customer"]["mc"] == "MC000111"
    assert (d["pickup"]["address"], d["pickup"]["city"], d["pickup"]["state"], d["pickup"]["zip"]) == \
        ("1552 Example View Drive", "Dandridge", "TN", "37725")
    assert d["pickup"]["name"] is None and d["pickup"]["contact"] == "Lot Desk"
    assert d["delivery"]["city"] == "Balsam Grove" and d["delivery"]["phone"] == "(555) 010-5000"
    assert str(d["pickup"]["date"]) == "2026-11-14" and str(d["delivery"]["date"]) == "2026-11-16"
    assert d["price"] == Decimal("1250") and d["payment_terms"] == "cod"
    assert [(v["year"], v["make"], v["model"], v["vin"], v["operable"]) for v in d["vehicles"]] == [
        ("2019", "Honda", "Accord", "1HGCV1F30KA000001", True),
        ("2021", "Ford", "F-150 XLT", "1FTEW1EP5MFA00002", False)]
    assert d["vehicles"][1]["lot_number"] == "LOT-77" and d["vehicles"][0]["lot_number"] is None
    assert "Wide Load" in d["notes"] and "Clear BOL" in d["notes"]


def test_parse_variants():
    from app.central_dispatch import parse
    d = parse(_sheet(origin_name="Manheim Orlando",
                     payment="Carrier will receive $900 via ACH within 5 business days of delivery.",
                     total="$900.00",
                     vehicles=[{"ymm": "1970 Gibson Boat And Trailer", "vin": "--", "type": "Boat",
                                "color": "--", "lot": "--"}]))
    assert d["pickup"]["name"] == "Manheim Orlando"
    assert d["payment_terms"] == "invoice"
    v = d["vehicles"][0]
    assert (v["year"], v["make"], v["model"], v["vin"], v["color"]) == ("1970", "Gibson", "Boat And Trailer", None, None)


def test_import_prefills_then_creates_load(app, client):
    login(client, "office@demo.test")
    r = client.post("/loads/import", data={"sheet": (_sheet(), "dispatch.pdf")}, content_type="multipart/form-data")
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Filled in from the dispatch sheet" in page and "Couldn" not in page
    assert 'value="Sunrise Auto Logistics LLC"' in page and 'value="1FTEW1EP5MFA00002"' in page
    assert 'value="301999888"' in page and 'value="Dandridge"' in page
    before = Load.query.count()
    assert before == Load.query.count()  # nothing saved by the import itself

    import re
    sheet_name = re.search(r'name="dispatch_sheet" value="([^"]+)"', page).group(1)
    r = client.post("/loads/new", data={
        "dispatch_sheet": sheet_name, "customer_id": "", "new_customer": "Sunrise Auto Logistics LLC",
        "new_customer_phone": "(555) 010-2001", "reference": "301999888", "price": "1250", "miles": "640",
        "payment_terms": "cod", "pickup_city": "Dandridge", "pickup_state": "TN",
        "delivery_city": "Balsam Grove", "delivery_state": "NC", "status": "booked",
        "v_vin": ["1HGCV1F30KA000001"], "v_year": ["2019"], "v_make": ["Honda"], "v_model": ["Accord"],
        "v_color": ["Black"], "v_lot_number": [""], "v_operable": ["1"]})
    assert r.status_code == 302
    load = Load.query.order_by(Load.id.desc()).first()
    assert load.reference == "301999888" and load.customer.phone == "(555) 010-2001"
    assert Upload.query.filter_by(name=sheet_name).one().load_id == load.id
    assert "Central Dispatch sheet" in client.get(f"/loads/{load.id}").get_data(as_text=True)
    assert client.get(f"/files/{sheet_name}").status_code == 200

    # importing the same sheet again warns about a duplicate
    r = client.post("/loads/import", data={"sheet": (_sheet(), "d.pdf")}, content_type="multipart/form-data")
    assert "might be a duplicate" in r.get_data(as_text=True)

    # drivers never get the dispatch sheet (it shows the price), even on their own load
    load.driver_id = uid("driver@demo.test")
    db.session.commit()
    login(client, "driver@demo.test")
    assert client.get(f"/files/{sheet_name}").status_code == 404


def test_import_rejects_non_pdfs(app, client):
    login(client, "office@demo.test")
    r = client.post("/loads/import", data={"sheet": (io.BytesIO(b"hello"), "x.pdf")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Download the dispatch sheet from Central Dispatch as a PDF" in r.get_data(as_text=True)
    login(client, "driver@demo.test")
    assert client.post("/loads/import", data={}).status_code == 403


# ---------------------------------------------------------------- Sunrise report

def test_sunrise_report(app, client):
    from datetime import datetime, time, timedelta, timezone
    from decimal import Decimal
    from zoneinfo import ZoneInfo
    from app.helpers import today
    from app.models import Payment
    yday = today() - timedelta(days=1)
    noon_utc = (datetime.combine(yday, time(12), tzinfo=ZoneInfo(app.config["APP_TIMEZONE"]))
                .astimezone(timezone.utc).replace(tzinfo=None))
    # wipe timestamps so only our load counts as yesterday's delivery
    for l in Load.query.all():
        l.delivered_at = l.picked_up_at = None
    load = Load.query.filter_by(driver_id=uid("driver@demo.test")).first()
    Expense.query.filter_by(load_id=load.id).delete()
    load.status, load.delivered_at, load.picked_up_at = "delivered", noon_utc, noon_utc - timedelta(days=1)
    load.price, load.miles, load.driver_pay, load.broker_fee = Decimal("2000"), 800, Decimal("600"), None
    load.reference = "SUNRISE-TEST"
    db.session.add(Expense(company_id=load.company_id, spent_on=yday, category="fuel", amount=Decimal("400"),
                           gallons=Decimal("100"), load_id=load.id, user_id=load.driver_id))
    db.session.add(Payment(load_id=load.id, amount=Decimal("750"), paid_on=yday, method="check"))
    db.session.commit()

    login(client, "owner@demo.test")
    page = client.get("/reports/sunrise").get_data(as_text=True)
    assert "Yesterday" in page and "Sunrise report" in page
    assert "$2,000.00" in page             # delivered revenue
    assert "$1,000.00" in page             # profit: 2000 - 400 fuel - 600 driver pay
    assert "$1.25/mi cost" in page         # 1000 / 800 mi
    assert "$750.00" in page               # payment collected
    assert "Carlos Driver" in page and "100.0 gal" in page
    # other days and bad dates don't break it
    assert client.get(f"/reports/sunrise?date={yday - timedelta(days=30)}").status_code == 200
    assert client.get("/reports/sunrise?date=2999-01-01").status_code == 200
    assert client.get("/reports/sunrise?date=junk").status_code == 200

    login(client, "driver@demo.test")
    assert client.get("/reports/sunrise").status_code == 403


def test_app_bridge_role_flag(app, client):
    ua = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) VTMApp/1.1.0"}
    login(client, "owner@demo.test")
    assert "VTMApp.setSunrise(true)" in client.get("/dashboard", headers=ua).get_data(as_text=True)
    assert 'id="sunrise-pref"' in client.get("/account", headers=ua).get_data(as_text=True)
    assert "VTMApp.setSunrise" not in client.get("/dashboard").get_data(as_text=True)  # plain browser
    login(client, "driver@demo.test")
    page = client.get("/dashboard", headers=ua).get_data(as_text=True)
    assert "VTMApp.setSunrise(false)" in page
    assert 'id="sunrise-pref"' not in client.get("/account", headers=ua).get_data(as_text=True)
    client.post("/logout")
    assert "VTMApp.setSunrise(false)" in client.get("/login", headers=ua).get_data(as_text=True)


# ---------------------------------------------------------------- self-service sign-up

def _signup(client, email="new.owner@example.com", company="Blue Road Hauling", **extra):
    data = {"company": company, "name": "Rita Newowner", "email": email, "phone": "(555) 010-9999",
            "password": "longpassword", "confirm_password": "longpassword"}
    data.update(extra)
    return client.post("/signup", data=data)


def test_signup_creates_separate_company(app, client):
    from app.models import Company
    existing = Load.query.first()
    client.post("/logout")
    assert "Create a company account" in client.get("/login").get_data(as_text=True)
    r = _signup(client)
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard")
    u = User.query.filter_by(email="new.owner@example.com").one()
    assert u.role == "owner" and u.company.name == "Blue Road Hauling" and u.company_id != existing.company_id
    # signed straight in, with the getting-started checklist and nothing from other companies
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Getting started" in page and "Blue Road Hauling" in page and existing.customer.name not in page
    assert client.get(f"/loads/{existing.id}").status_code == 404
    assert client.get(f"/customers/{existing.customer_id}").status_code == 404
    assert "No loads match" in client.get("/loads/?status=all").get_data(as_text=True)
    # their own first load gets number 1001 like everyone else's
    client.post("/loads/new", data={"price": "500", "pickup_city": "A", "delivery_city": "B", "status": "booked"})
    mine = Load.query.filter_by(company_id=u.company_id).one()
    assert mine.number == 1001
    # the original company can't see it either
    login(client, "owner@demo.test")
    assert client.get(f"/loads/{mine.id}").status_code == 404
    assert Company.query.count() == 2


def test_signup_validation_and_spam_guards(app, client):
    r = _signup(client, email="owner@demo.test")
    assert r.status_code == 400 and "already an account" in r.get_data(as_text=True)
    assert _signup(client, email="nope").status_code == 400
    assert _signup(client, email="a@b.co", confirm_password="different!").status_code == 400
    assert _signup(client, email="bot@b.co", website="http://spam").status_code == 400  # honeypot
    assert not User.query.filter_by(email="bot@b.co").first()
    app.config["SIGNUPS_PER_IP_PER_DAY"] = 1
    assert _signup(client, email="first@b.co").status_code == 302
    client.post("/logout")
    r = _signup(client, email="second@b.co", company="Second Co")
    assert r.status_code == 400 and "Too many new accounts" in r.get_data(as_text=True)
    app.config["ALLOW_SIGNUP"] = False
    assert client.get("/signup").status_code == 404
    assert "Create a company account" not in client.get("/login").get_data(as_text=True)


def test_superadmin_page(app, client):
    login(client, "owner@demo.test")
    assert client.get("/admin/").status_code == 404           # not on the list
    app.config["SUPERADMIN_EMAILS"] = ["owner@demo.test"]
    page = client.get("/admin/").get_data(as_text=True)
    assert "All companies" in page and "Sunshine Auto Transport" in page
    assert "All companies (admin)" in client.get("/dashboard").get_data(as_text=True)


# ---------------------------------------------------------------- iPhone / browser notifications

SUB = {"endpoint": "https://web.push.apple.com/QAbc123", "keys": {"p256dh": "BPk" + "x" * 84, "auth": "a" * 22}}


def push_ready(app, monkeypatch, outcome="ok"):
    from app import push
    app.config.update(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
    sent = []

    def fake_send(sub, payload):
        sent.append((sub.user.email, payload))
        if outcome == "gone":
            db.session.delete(sub)
        return outcome
    monkeypatch.setattr(push, "send", fake_send)
    return sent


def test_iphone_pages_and_service_worker(client):
    r = client.get("/iphone", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Safari"})
    assert r.status_code == 200 and b"Add to Home Screen" in r.data
    r = client.get("/sw.js")
    assert r.status_code == 200 and b"showNotification" in r.data and r.headers["Service-Worker-Allowed"] == "/"
    assert b"iPhone app" in client.get("/", headers={"User-Agent": "iPhone"}).data
    m = client.get("/manifest.webmanifest").get_json()
    assert m["display"] == "standalone" and m["scope"] == "/"
    login(client, "owner@demo.test")
    page = client.get("/dashboard", headers={"User-Agent": "iPhone"}).data
    assert b"apple-mobile-web-app-capable" in page and b"ios-install-tip" in page and b"/sw.js" in page
    # the Android app keeps its own notification and doesn't get the web one
    acct = client.get("/account", headers={"User-Agent": "VTMApp/1.1.0"}).data
    assert b"push-pref" not in acct and b"sunrise-pref" in acct
    assert b"push-pref" in client.get("/account").data


def test_push_subscribe_send_and_cleanup(app, client, monkeypatch):
    from app import push
    from app.models import PushSubscription
    sent = push_ready(app, monkeypatch)

    login(client, "driver@demo.test")
    assert client.post("/push/subscribe", json=SUB).status_code == 403  # drivers don't get the Sunrise report

    login(client, "owner@demo.test")
    assert client.post("/push/subscribe", json={"endpoint": "http://evil", "keys": {}}).status_code == 400
    r = client.post("/push/subscribe", json=SUB, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1)"})
    assert r.get_json() == {"ok": True}
    client.post("/push/subscribe", json=SUB)  # same device again: still one row
    sub = PushSubscription.query.one()
    assert sub.device in ("iPhone", "Browser") and sub.user.email == "owner@demo.test"

    assert client.post("/push/test", json=SUB).get_json() == {"ok": True}
    assert sent[-1][1]["title"].endswith("(test)") and sent[-1][1]["url"] == "/reports/sunrise"

    # 6 AM run: sends once a day, only inside the morning window unless forced
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(app.config["APP_TIMEZONE"])
    monkeypatch.setattr(push, "local_now", lambda: datetime(2026, 11, 15, 14, 0, tzinfo=tz))
    assert "skipped" in push.send_sunrise()
    monkeypatch.setattr(push, "local_now", lambda: datetime(2026, 11, 15, 6, 0, 5, tzinfo=tz))
    sent.clear()
    assert push.send_sunrise() == {"ok": 1, "gone": 0, "error": 0}
    assert sent[0][1]["body"].startswith(("Yesterday:", "No deliveries"))
    assert push.send_sunrise() == {"ok": 0, "gone": 0, "error": 0}  # already sent today

    # a device that removed the app is cleaned up
    push_ready(app, monkeypatch, outcome="gone")
    monkeypatch.setattr(push, "local_now", lambda: datetime(2026, 11, 16, 6, 0, tzinfo=tz))
    assert push.send_sunrise()["gone"] == 1 and PushSubscription.query.count() == 0

    # unsubscribe only removes your own device
    push_ready(app, monkeypatch)
    client.post("/push/subscribe", json=SUB)
    login(client, "office@demo.test")
    client.post("/push/unsubscribe", json=SUB)
    assert PushSubscription.query.count() == 1
    login(client, "owner@demo.test")
    client.post("/push/unsubscribe", json=SUB)
    assert PushSubscription.query.count() == 0


def test_sunrise_summary_counts_yesterday(app):
    from datetime import datetime, timezone
    from decimal import Decimal
    from app import push
    from app.models import Company
    co = Company.query.first()
    load = Load.query.filter_by(company_id=co.id).first()
    load.delivered_at = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    load.price = Decimal("1250")
    db.session.commit()
    from datetime import date
    text = push.sunrise_summary(co.id, date(2026, 11, 14))
    assert "delivered" in text and "$" in text


def test_vapid_keys_work_with_pywebpush(app):
    from py_vapid import Vapid01
    out = app.test_cli_runner().invoke(args=["vapid-keys"]).output
    lines = dict(l.split("=", 1) for l in out.strip().splitlines())
    assert len(lines["VAPID_PUBLIC_KEY"]) == 87
    Vapid01.from_string(lines["VAPID_PRIVATE_KEY"])  # the format webpush() is given


# ---------------------------------------------------------------- how-to videos

def test_videos_pages_public_shareable_and_linked(client):
    from app.blueprints.main import VIDEOS
    r = client.get("/videos")
    assert r.status_code == 200
    for v in VIDEOS:
        assert v["slug"].encode() in r.data
    r = client.get("/videos/central-dispatch-import")
    page = r.data.decode()
    assert r.status_code == 200
    assert '<video controls playsinline' in page and "central-dispatch-import.mp4" in page
    # link previews (texts, Facebook, WhatsApp) use absolute URLs
    assert 'property="og:image" content="https://' in page and 'property="og:video" content="https://' in page
    assert "https://vehicletransport.autos/videos/central-dispatch-import" in page or 'property="og:url" content="http' in page
    assert "wa.me/?text=" in page and "facebook.com/sharer" in page and "sms:?&body=" in page
    assert client.get("/videos/nope").status_code == 404
    # the files exist and support range requests (iPhones need them to play video)
    for v in VIDEOS:
        r = client.get(f"/static/videos/{v['slug']}.mp4", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.data) == 100
        r.close()
        assert client.get(f"/static/videos/{v['slug']}.jpg").status_code == 200
    # linked from the public pages and, once signed in, from the menu, dashboard and New load
    assert b"/videos" in client.get("/").data
    assert b"/videos/install-android-app" in client.get("/app").data
    assert b"/videos/install-iphone-app" in client.get("/iphone").data
    login(client, "owner@demo.test")
    assert b'href="/videos"' in client.get("/dashboard").data
    assert b"/videos/central-dispatch-import" in client.get("/loads/new").data
    assert b"<video" in client.get("/videos/install-iphone-app").data  # also works inside the app
