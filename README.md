# Vehicle Transport Manager — vehicletransport.autos

Business management for a vehicle transport (car-hauling) company: loads, dispatch, drivers,
photo condition reports with e-signatures, expenses, invoices, BOLs and profit reports.
It's a phone-first web app plus an Android app that wraps it with camera, GPS, downloads and printing.

## Who sees what

| | Owner | Office / Dispatch | Driver |
|---|---|---|---|
| Loads | all, create/edit/delete | all, create/edit | **only their own**, no prices (just their pay and any COD amount to collect) |
| Pickup / delivery inspections | ✓ | ✓ | ✓ (own loads) |
| Payments, invoices, customers, reports, CSV | ✓ | ✓ | — |
| Expenses | all | all, reviews driver expenses | logs their own (fuel, tolls, repairs…) with receipt photo |
| Team logins, company settings | ✓ | view team only | — |

The typical day: the office books a load (VIN decode fills in year/make/model) and assigns a driver.
At pickup the driver opens the load and taps **Start pickup inspection**, takes photos, notes any damage,
gets the shipper's signature, and saves. That marks the load *Picked up* and stamps the GPS location.
**Deliver & get signature** does the same at the other end and marks it *Delivered*. The office then
records the payment, which marks it *Paid*. The invoice and the BOL (with every photo and signature) can be
printed or saved as PDF from the load's ⋯ menu.

## Run it locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export APP_ENV=development          # SQLite in ./instance, cookies over http
flask --app wsgi seed-demo          # demo company; password demo1234 for
                                    # owner@demo.test, office@demo.test, driver@demo.test, driver2@demo.test
flask --app wsgi run -p 5055
python -m pytest -q                 # 9 end-to-end tests (roles, inspections, payments, isolation, CSRF…)
```

## Deploy (Ubuntu + MySQL + Cloudflare Tunnel)

1. **Rotate everything from the old project first** (see *Security* below).
2. Code in `/opt/vtm`, owned by a `vtm` system user:
   ```bash
   sudo useradd --system --home /opt/vtm vtm
   sudo mkdir -p /opt/vtm /var/lib/vtm/uploads && sudo chown -R vtm:vtm /opt/vtm /var/lib/vtm
   # copy this folder to /opt/vtm, then as vtm:
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
3. Database: edit the password in `deploy/mysql_setup.sql`, then run `sudo mysql < deploy/mysql_setup.sql`.
4. `cp .env.example .env` and fill it in (`SECRET_KEY`, `DATABASE_URL` with the new password, `UPLOAD_FOLDER=/var/lib/vtm/uploads`). Then `chmod 600 .env`.
5. Create the tables and your friend's owner login:
   ```bash
   .venv/bin/flask --app wsgi init-db
   .venv/bin/flask --app wsgi create-owner      # prompts for company, name, email, password
   ```
6. Service: `sudo cp deploy/vtm.service /etc/systemd/system/ && sudo systemctl enable --now vtm`
7. Tunnel: point `vehicletransport.autos` at `http://127.0.0.1:5055` (see `deploy/cloudflared-config.yml`).
   In Cloudflare, set SSL/TLS to *Full* and turn on *Always Use HTTPS*.
8. Backups: `deploy/backup.sh` dumps MySQL and tars the uploads folder. Run it nightly from root's cron
   (MySQL credentials come from `/root/.my.cnf`). **The uploads folder holds the inspection photos and
   signatures, which are the company's evidence in damage claims, so back it up.**

The owner adds everyone else from **Team → Add person**. The app generates a temporary password and a
copy-paste message to send them.

Other commands: `flask --app wsgi reset-password` (CLI password reset).

## Android app

The app lives in `android/` (Kotlin, no third-party libraries apart from `androidx.core`). It opens
`https://vehicletransport.autos` and adds:

- camera / gallery for inspection photos and receipts (the web page also shrinks photos before upload, which saves drivers' data)
- GPS for pickup and delivery location stamps
- CSV downloads that keep the login
- **Print / Save as PDF** for invoices and BOLs
- an **update prompt**: on launch it reads `/app/version.json` and offers the newer APK
- a friendly *No connection* screen with retry

### Build it (no Android Studio needed)

Push this repo to GitHub, then go to **Actions → Android app → Run workflow** and enter a version such as `1.0.0`.
The APKs show up under the run's **Artifacts**.

To get an installable **release** APK, create a signing key once and add it as repository secrets.
Keep the key safe: every future update must be signed with the same key, or phones will refuse to install it.

```bash
keytool -genkeypair -v -keystore vtm-release.jks -alias vtm -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 vtm-release.jks   # → secret VTM_KEYSTORE_BASE64
# also add secrets VTM_KEYSTORE_PASSWORD, VTM_KEY_ALIAS (=vtm), VTM_KEY_PASSWORD
```

Without the secrets the workflow still builds a **debug** APK. It installs and works fine for testing
(app id `autos.vehicletransport.app.debug`).

Local build, if you have the Android SDK: `cd android && ./gradlew assembleDebug`
(`-PbaseUrl=https://staging.example.com` points it at another server).

### Publish it on the site

1. Copy the APK to `app/static/downloads/vtm.apk` on the server.
2. In `.env`, set `ANDROID_VERSION_CODE` to the build's versionCode (shown in the Actions run summary) and
   `ANDROID_VERSION_NAME` to the version, then `sudo systemctl restart vtm`.
3. Installed apps will offer the update the next time they open. New users go to
   **vehicletransport.autos/app**, which has step-by-step install instructions.

## Project layout

```
app/
  __init__.py        app factory, security headers, login loader
  config.py          everything from environment variables
  models.py          Company, User(roles), Customer, Truck, Load, Vehicle, LoadEvent,
                     Payment, Inspection(+Photo), Expense, Upload
  helpers.py         role guards, company/driver scoping, upload handling
  blueprints/        auth, main(dashboard, files, app download), loads, inspections,
                     customers, expenses, team, trucks, reports, settings
  templates/ static/ Bootstrap 5 (vendored, no CDN), app.css, app.js
android/             Android app (Kotlin)
deploy/              systemd unit, MySQL setup, cloudflared config, backup script
tests/               pytest suite
```

Every record carries `company_id`, so the shelved multi-company SaaS mode (subscriptions, sign-up,
ambassador program from the old code) can be added back later without reworking the schema.

## Security

- **The old project's credentials are compromised.** It had the MySQL password, Flask secret key,
  Stripe keys and webhook secret, and a Gmail app password hard-coded in plain text. Revoke the Gmail
  app password (Google Account → Security → App passwords). Roll the Stripe keys if you ever use that
  Stripe account again. Use a brand-new MySQL user and password. None of the old code is reused here.
- Passwords are hashed with Werkzeug's scrypt/pbkdf2. Logins lock for 15 minutes after 8 failed attempts.
  Changing a password or deactivating a user signs that person out on every device.
- CSRF protection on every form. Session cookies are Secure, HttpOnly and SameSite=Lax.
- Photos, signatures and receipts are stored outside `/static` and served only to logged-in users of
  the same company. A driver can only open files from their own loads. Photos are re-encoded on upload,
  which strips EXIF data and rejects anything that isn't really an image.
