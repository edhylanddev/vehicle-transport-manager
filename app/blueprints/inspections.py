"""Pickup / delivery condition reports (photos, damage notes, signature, GPS)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..helpers import finish, f_float, f_str, get_load_or_404, save_image, save_signature
from ..models import Inspection, InspectionPhoto
from .loads import log, set_status

bp = Blueprint("inspections", __name__)

KINDS = {"pickup": ("Pickup", "picked_up", ("assigned", "booked")),
         "delivery": ("Delivery", "delivered", ("picked_up",))}
MAX_PHOTOS = 40


def _can_inspect(load):
    return current_user.is_staff or load.driver_id == current_user.id


@bp.route("/loads/<int:load_id>/inspect/<kind>", methods=["GET", "POST"])
@login_required
def inspect(load_id, kind):
    if kind not in KINDS:
        abort(404)
    load = get_load_or_404(load_id)
    if not _can_inspect(load):
        abort(403)
    label, next_status, allowed_from = KINDS[kind]
    existing = load.inspection(kind)
    if existing:
        return redirect(url_for("inspections.view", inspection_id=existing.id))
    if load.status not in allowed_from and not current_user.is_staff:
        flash(f"This load isn't ready for {label.lower()} yet.", "warning")
        return redirect(url_for("loads.detail", load_id=load.id))

    if request.method == "POST":
        insp = Inspection(load_id=load.id, kind=kind, driver_id=current_user.id,
                          damage_notes=f_str("damage_notes", 5000), odometer=f_str("odometer", 20),
                          signer_name=f_str("signer_name", 120), lat=f_float("lat"), lng=f_float("lng"))
        insp.signature_file = save_signature(request.form.get("signature"), load.id)
        bad = 0
        for fs in request.files.getlist("photos")[:MAX_PHOTOS]:
            name = save_image(fs, f"insp-{load.id}-{kind}", load_id=load.id)
            if name:
                insp.photos.append(InspectionPhoto(file=name))
            elif fs and fs.filename:
                bad += 1
        db.session.add(insp)
        n = len(insp.photos)
        log(load, f"{label} inspection: {n} photo{'s' if n != 1 else ''}"
                  + (", signed by " + insp.signer_name if insp.signer_name else "")
                  + (", damage noted" if insp.damage_notes else ""),
            kind="inspection", lat=insp.lat, lng=insp.lng)
        if load.status in allowed_from or (current_user.is_staff and load.status not in ("delivered", "paid", "cancelled")):
            set_status(load, next_status)
        db.session.commit()
        if bad:
            flash(f"{bad} file(s) weren't images and were skipped.", "warning")
        flash(f"{label} saved — load #{load.number} is now {load.status_label}.", "success")
        return finish(url_for("loads.detail", load_id=load.id))

    return render_template("inspections/form.html", load=load, kind=kind, label=label)


@bp.route("/inspections/<int:inspection_id>")
@login_required
def view(inspection_id):
    insp = db.session.get(Inspection, inspection_id) or abort(404)
    load = get_load_or_404(insp.load_id)  # enforces company + driver visibility
    return render_template("inspections/view.html", insp=insp, load=load,
                           label=KINDS[insp.kind][0])


@bp.route("/inspections/<int:inspection_id>/photos", methods=["POST"])
@login_required
def add_photos(inspection_id):
    insp = db.session.get(Inspection, inspection_id) or abort(404)
    load = get_load_or_404(insp.load_id)
    if not _can_inspect(load):
        abort(403)
    added = 0
    for fs in request.files.getlist("photos")[:MAX_PHOTOS]:
        name = save_image(fs, f"insp-{load.id}-{insp.kind}", load_id=load.id)
        if name:
            insp.photos.append(InspectionPhoto(file=name))
            added += 1
    if added:
        log(load, f"Added {added} photo(s) to {insp.kind} inspection", kind="inspection")
    db.session.commit()
    flash(f"Added {added} photo(s).", "success" if added else "warning")
    return finish(url_for("inspections.view", inspection_id=insp.id))
