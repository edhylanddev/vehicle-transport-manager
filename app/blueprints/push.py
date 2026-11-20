"""Turning the Sunrise notification on and off for a browser or iPhone home-screen app."""
from datetime import timedelta

from flask import Blueprint, jsonify, request
from flask_login import current_user

from ..extensions import db
from ..helpers import staff_required
from ..models import PushSubscription
from .. import push

bp = Blueprint("push", __name__, url_prefix="/push")


def _sub_from_json():
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get("endpoint") or "")
    keys = data.get("keys") or {}
    if not endpoint.startswith("https://") or len(endpoint) > 700:
        return None, None
    return endpoint, keys


@bp.route("/subscribe", methods=["POST"])
@staff_required
def subscribe():
    if not push.enabled():
        return jsonify(ok=False, error="Notifications aren't set up on the server yet."), 503
    endpoint, keys = _sub_from_json()
    p256dh, auth = str(keys.get("p256dh") or "") if keys else "", str(keys.get("auth") or "") if keys else ""
    if not endpoint or not p256dh or not auth or len(p256dh) > 200 or len(auth) > 100:
        return jsonify(ok=False, error="That browser sent an invalid subscription."), 400
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first() or PushSubscription(endpoint=endpoint)
    sub.user_id, sub.company_id = current_user.id, current_user.company_id
    sub.p256dh, sub.auth = p256dh, auth
    sub.device = push.device_name(request.headers.get("User-Agent"))
    db.session.add(sub)
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/unsubscribe", methods=["POST"])
@staff_required
def unsubscribe():
    endpoint, _ = _sub_from_json()
    if endpoint:
        PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user.id).delete()
        db.session.commit()
    return jsonify(ok=True)


@bp.route("/test", methods=["POST"])
@staff_required
def test():
    endpoint, _ = _sub_from_json()
    sub = PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user.id).first() if endpoint else None
    if not sub:
        return jsonify(ok=False, error="This device isn't signed up for notifications. Turn the switch off and on again."), 404
    yesterday = push.local_now().date() - timedelta(days=1)
    result = push.send(sub, {"title": "☀️ Sunrise report (test)", "url": "/reports/sunrise", "tag": "sunrise-test",
                             "body": push.sunrise_summary(current_user.company_id, yesterday)})
    db.session.commit()
    if result == "ok":
        return jsonify(ok=True)
    msg = ("This device turned notifications off. Turn the switch off and on again." if result == "gone"
           else "Couldn't reach the notification service. Try again in a minute.")
    return jsonify(ok=False, error=msg), 502
