"""Shared helpers: role guards, scoped queries, uploads, form parsing."""
import base64
import os
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import BytesIO

from flask import abort, current_app, jsonify, redirect, request
from flask_login import current_user, login_required
from PIL import Image, ImageOps

from .extensions import db
from .models import Load, Upload

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "heic", "heif", "gif"}


# ---------------------------------------------------------------- access control

def staff_required(f):
    """Owner or office staff only."""
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if not current_user.is_staff:
            abort(403)
        return f(*a, **kw)
    return wrapper


def owner_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if not current_user.is_owner:
            abort(403)
        return f(*a, **kw)
    return wrapper


def scoped(model):
    """Query limited to the current user's company."""
    return model.query.filter_by(company_id=current_user.company_id)


def get_or_404_scoped(model, obj_id):
    obj = scoped(model).filter_by(id=obj_id).first()
    if obj is None:
        abort(404)
    return obj


def visible_loads():
    """Loads the current user may see: all for staff, only their own for drivers."""
    q = scoped(Load)
    if current_user.is_driver:
        q = q.filter(Load.driver_id == current_user.id)
    return q


def get_load_or_404(load_id):
    load = visible_loads().filter(Load.id == load_id).first()
    if load is None:
        abort(404)
    return load


def nulls_last(col, desc=False):
    """Portable NULLS LAST ordering (MySQL has no NULLS LAST keyword)."""
    return (col.is_(None), col.desc() if desc else col.asc())


# ---------------------------------------------------------------- parsing

def f_str(name, maxlen=None):
    v = (request.form.get(name) or "").strip()
    if maxlen:
        v = v[:maxlen]
    return v or None


def f_money(name):
    raw = (request.form.get(name) or "").replace("$", "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def f_int(name):
    raw = (request.form.get(name) or "").replace(",", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def f_float(name):
    raw = (request.form.get(name) or "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def f_date(name, default=None):
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return default


def arg_date(name, default):
    raw = (request.args.get(name) or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else default
    except ValueError:
        return default


# ---------------------------------------------------------------- uploads

def _upload_dir():
    path = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(path, exist_ok=True)
    return path


def _register(name, load_id=None):
    db.session.add(Upload(company_id=current_user.company_id, name=name, load_id=load_id,
                          uploaded_by_id=current_user.id))


def save_image(file_storage, prefix, load_id=None):
    """Validate, auto-rotate, shrink and re-encode an uploaded photo as JPEG.

    Re-encoding strips EXIF (incl. phone GPS tags) and anything that isn't
    really an image. Returns the stored filename or None.
    """
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext and ext not in ALLOWED_IMAGE_EXT:
        return None
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_px = current_app.config["PHOTO_MAX_PX"]
        img.thumbnail((max_px, max_px))
    except Exception:
        return None
    name = f"{prefix}-{uuid.uuid4().hex}.jpg"
    img.save(os.path.join(_upload_dir(), name), "JPEG", quality=82, optimize=True)
    _register(name, load_id)
    return name


def save_signature(data_url, load_id):
    """Store a signature-pad PNG data URL."""
    if not data_url or not data_url.startswith("data:image/png;base64,"):
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        if len(raw) > 2 * 1024 * 1024:
            return None
        img = Image.open(BytesIO(raw))
        img.verify()
    except Exception:
        return None
    name = f"sig-{load_id}-{uuid.uuid4().hex}.png"
    with open(os.path.join(_upload_dir(), name), "wb") as fh:
        fh.write(raw)
    _register(name, load_id)
    return name


def money(v):
    if v is None:
        return "—"
    v = Decimal(v)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def today():
    """Today's date in the company's time zone (server may run in UTC)."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(current_app.config["APP_TIMEZONE"])).date()


def finish(url):
    """Redirect, or for the JS uploader (XHR) return the target URL as JSON so the
    browser navigates once and flash messages survive."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(redirect=url)
    return redirect(url)
