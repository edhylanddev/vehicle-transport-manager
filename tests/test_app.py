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
