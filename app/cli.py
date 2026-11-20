"""flask CLI commands: init-db, create-owner, reset-password, vapid-keys, send-sunrise, seed-demo."""
import random
from datetime import date, timedelta
from decimal import Decimal

import click

from .extensions import db
from .models import (Company, Customer, Expense, Load, LoadEvent, Payment, Truck, User, Vehicle,
                     ROLE_DRIVER, ROLE_OFFICE, ROLE_OWNER, utcnow)


def register(app):
    @app.cli.command("init-db")
    def init_db():
        """Create all tables (safe to run again; existing tables are left alone)."""
        db.create_all()
        click.echo("Tables created.")

    @app.cli.command("create-owner")
    @click.option("--company", prompt="Company name")
    @click.option("--name", prompt="Owner's name")
    @click.option("--email", prompt="Owner's email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_owner(company, name, email, password):
        """Create the company and its owner login."""
        db.create_all()
        if User.query.filter_by(email=email.strip().lower()).first():
            raise click.ClickException("A user with that email already exists.")
        if len(password) < 8:
            raise click.ClickException("Password must be at least 8 characters.")
        co = Company.query.filter_by(name=company).first() or Company(name=company)
        db.session.add(co)
        db.session.flush()
        u = User(company_id=co.id, name=name, email=email.strip().lower(), role=ROLE_OWNER)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Owner {email} created for {company}.")

    @app.cli.command("reset-password")
    @click.option("--email", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def reset_password(email, password):
        u = User.query.filter_by(email=email.strip().lower()).first()
        if not u:
            raise click.ClickException("No such user.")
        u.set_password(password)
        u.failed_logins = 0
        u.locked_until = None
        db.session.commit()
        click.echo("Password updated; the user is signed out everywhere.")

    @app.cli.command("vapid-keys")
    def vapid_keys():
        """Print a new key pair for iPhone/browser notifications (add the lines to .env, then restart)."""
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        def b64(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_numbers().private_value.to_bytes(32, "big")
        public = key.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
        click.echo("VAPID_PUBLIC_KEY=" + b64(public))
        click.echo("VAPID_PRIVATE_KEY=" + b64(private))

    @app.cli.command("send-sunrise")
    @click.option("--force", is_flag=True, help="Send even outside the morning window.")
    def send_sunrise(force):
        """Send the Sunrise notification to iPhones and browsers (a systemd timer runs this at 6 AM)."""
        from .push import send_sunrise as run
        click.echo(run(force=force))

    @app.cli.command("seed-demo")
    def seed_demo():
        """Fill an EMPTY database with a demo company (for trying the app out)."""
        db.create_all()
        if Company.query.first():
            raise click.ClickException("Database is not empty; refusing to seed.")
        seed(password="demo1234")
        click.echo("Demo data created. Logins (password demo1234): "
                   "owner@demo.test, office@demo.test, driver@demo.test, driver2@demo.test")


def seed(password="demo1234", n_loads=40):
    rnd = random.Random(7)
    co = Company(name="Sunshine Auto Transport", mc_number="MC-000000", dot_number="0000000",
                 phone="(772) 555-0100", email="dispatch@example.com",
                 address="Port St. Lucie, FL")
    db.session.add(co)
    db.session.flush()

    def mk(name, email, role):
        u = User(company_id=co.id, name=name, email=email, role=role, phone="(772) 555-01" + str(rnd.randint(10, 99)))
        u.set_password(password)
        db.session.add(u)
        return u

    owner = mk("Mike Owner", "owner@demo.test", ROLE_OWNER)
    mk("Dana Dispatch", "office@demo.test", ROLE_OFFICE)
    d1 = mk("Carlos Driver", "driver@demo.test", ROLE_DRIVER)
    d2 = mk("Jess Driver", "driver2@demo.test", ROLE_DRIVER)
    t1 = Truck(company_id=co.id, unit="T-1", description="Peterbilt 389 + 9-car Cottrell", capacity=9)
    t2 = Truck(company_id=co.id, unit="T-2", description="F-450 + 3-car wedge", capacity=3)
    db.session.add_all([t1, t2])

    custs = [Customer(company_id=co.id, name=n, kind=k, phone="(800) 555-0" + str(100 + i),
                      email=f"orders{i}@example.com", contact_name=c)
             for i, (n, k, c) in enumerate([
                 ("Coastal Auto Brokers", "broker", "Rita"),
                 ("Treasure Coast Motors", "dealer", "Sam"),
                 ("Manheim Palm Beach", "auction", "Front desk"),
                 ("Ridgeway Logistics", "broker", "Tom"),
                 ("John Smith", "private", None)])]
    db.session.add_all(custs)
    db.session.flush()

    cities = [("Port St. Lucie", "FL"), ("Orlando", "FL"), ("Miami", "FL"), ("Atlanta", "GA"),
              ("Jacksonville", "FL"), ("Charlotte", "NC"), ("Tampa", "FL"), ("Savannah", "GA")]
    cars = [("2021", "Toyota", "Camry"), ("2019", "Honda", "Accord"), ("2022", "Ford", "F-150"),
            ("2018", "Chevrolet", "Silverado"), ("2020", "Tesla", "Model 3"), ("2017", "Jeep", "Wrangler"),
            ("2023", "Kia", "Telluride"), ("2016", "BMW", "X5")]
    today = date.today()
    made = []
    for i in range(n_loads):
        pu = rnd.choice(cities)
        de = rnd.choice([c for c in cities if c != pu])
        days_ago = rnd.randint(-5, 85)
        pickup = today - timedelta(days=days_ago)
        price = Decimal(rnd.randrange(350, 1900, 25))
        driver = rnd.choice([d1, d2])
        if days_ago < 0:
            status = rnd.choice(["booked", "assigned"])
        elif days_ago < 2:
            status = rnd.choice(["assigned", "picked_up"])
        elif days_ago < 12:
            status = rnd.choice(["picked_up", "delivered", "delivered"])
        else:
            status = rnd.choice(["delivered", "paid", "paid", "paid", "cancelled"])
        load = Load(company_id=co.id, number=co.next_load_number, status=status,
                    customer_id=rnd.choice(custs).id, reference=f"CD-{rnd.randint(100000, 999999)}",
                    pickup_city=pu[0], pickup_state=pu[1], pickup_date=pickup,
                    pickup_contact="Lot manager", pickup_phone="(555) 555-0111",
                    pickup_address=f"{rnd.randint(100, 9999)} Main St",
                    delivery_city=de[0], delivery_state=de[1], delivery_date=pickup + timedelta(days=rnd.randint(1, 3)),
                    delivery_address=f"{rnd.randint(100, 9999)} Commerce Blvd",
                    delivery_contact="Receiving", delivery_phone="(555) 555-0122",
                    miles=rnd.randint(80, 900), price=price,
                    driver_pay=(price * Decimal("0.30")).quantize(Decimal("1")),
                    payment_terms=rnd.choice(["cod", "quick_pay", "invoice"]),
                    driver_id=driver.id if status != "booked" else None,
                    truck_id=(t1 if driver == d1 else t2).id if status != "booked" else None)
        co.next_load_number += 1
        for _ in range(rnd.choice([1, 1, 1, 2, 3])):
            y, mk_, md = rnd.choice(cars)
            load.vehicles.append(Vehicle(year=y, make=mk_, model=md, color=rnd.choice(["White", "Black", "Silver", "Blue", "Red"]),
                                         vin="".join(rnd.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(17))))
        if status in ("picked_up", "delivered", "paid"):
            load.picked_up_at = utcnow() - timedelta(days=max(days_ago, 0))
        if status in ("delivered", "paid"):
            load.delivered_at = utcnow() - timedelta(days=max(days_ago - 1, 0))
        if status == "paid":
            load.payments.append(Payment(amount=price, paid_on=pickup + timedelta(days=rnd.randint(1, 10)),
                                         method=rnd.choice(["check", "ach", "zelle"]), recorded_by_id=owner.id))
        load.events.append(LoadEvent(user_id=owner.id, kind="status", message=f"Load created ({status})"))
        db.session.add(load)
        made.append(load)

    # 1-3 fuel stops on each delivered/paid load, so job cost per mile has data
    db.session.flush()
    for load in made:
        if load.status in ("picked_up", "delivered", "paid") and load.driver_id:
            for _ in range(rnd.choice([1, 2, 2, 3])):
                gal = Decimal(str(round(rnd.uniform(40, 110), 1)))
                ppg = Decimal(str(round(rnd.uniform(3.55, 4.15), 3)))
                db.session.add(Expense(company_id=co.id, spent_on=load.pickup_date or today, category="fuel",
                                       amount=(gal * ppg).quantize(Decimal("0.01")), gallons=gal,
                                       vendor=rnd.choice(["Pilot", "Love's", "TA", "Wawa"]),
                                       user_id=load.driver_id, truck_id=load.truck_id, load_id=load.id))

    for i in range(60):
        d = today - timedelta(days=rnd.randint(0, 85))
        cat = rnd.choice(["fuel"] * 6 + ["tolls", "tolls", "repairs", "meals", "lodging", "insurance", "dispatch"])
        amt = {"fuel": rnd.uniform(180, 650), "tolls": rnd.uniform(8, 60), "repairs": rnd.uniform(90, 1400),
               "meals": rnd.uniform(10, 40), "lodging": rnd.uniform(70, 140), "insurance": 1250,
               "dispatch": 149}[cat]
        drv = rnd.choice([d1, d2])
        db.session.add(Expense(company_id=co.id, spent_on=d, category=cat, amount=Decimal(str(round(amt, 2))),
                               vendor={"fuel": "Pilot", "tolls": "SunPass", "repairs": "Rush Truck Center"}.get(cat),
                               gallons=Decimal(str(round(amt / 3.9, 1))) if cat == "fuel" else None,
                               user_id=drv.id, truck_id=(t1 if drv == d1 else t2).id))
    db.session.commit()
    return co
