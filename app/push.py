"""Web push: the 6 AM Sunrise notification for iPhones (home-screen app) and desktop/Android browsers.

The Android app schedules its own notification; everything else gets it from the server through the
browser's push service (Apple, Google, Mozilla). Payloads are end-to-end encrypted by the Web Push protocol.
"""
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import current_app

from .extensions import db
from .models import Load, PushSubscription, User, utcnow

log = logging.getLogger(__name__)


def enabled():
    cfg = current_app.config
    return bool(cfg["VAPID_PUBLIC_KEY"] and cfg["VAPID_PRIVATE_KEY"])


def device_name(ua):
    ua = ua or ""
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua or ("Macintosh" in ua and "Mobile" in ua):
        return "iPad"
    if "Android" in ua:
        return "Android browser"
    if "Mac OS X" in ua:
        return "Mac"
    if "Windows" in ua:
        return "Windows PC"
    return "Browser"


def send(sub, payload):
    """Send one notification. Returns "ok", "gone" (subscription expired, deleted here) or "error"."""
    from pywebpush import WebPushException, webpush
    cfg = current_app.config
    try:
        webpush(sub.info(), json.dumps(payload), vapid_private_key=cfg["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": cfg["VAPID_SUBJECT"]}, ttl=4 * 3600, timeout=15)
        return "ok"
    except WebPushException as e:
        code = getattr(e.response, "status_code", None)
        if code in (404, 410):  # the user turned notifications off or removed the app
            db.session.delete(sub)
            return "gone"
        log.warning("push to %s failed: %s", sub.device, e)
        return "error"
    except Exception as e:  # noqa: BLE001 - network trouble must not stop the other sends
        log.warning("push to %s failed: %s", sub.device, e)
        return "error"


def _money(v):
    return "${:,.0f}".format(v)


def local_now():
    return datetime.now(ZoneInfo(current_app.config["APP_TIMEZONE"]))


def sunrise_summary(company_id, day):
    """One line about a company's day, for the notification body."""
    from .blueprints.reports import _utc_bounds
    start, end = _utc_bounds(day)
    q = Load.query.filter(Load.company_id == company_id)
    delivered = q.filter(Load.delivered_at >= start, Load.delivered_at < end).all()
    picked = q.filter(Load.picked_up_at >= start, Load.picked_up_at < end).count()
    booked = q.filter(Load.created_at >= start, Load.created_at < end).count()
    if not (delivered or picked or booked):
        return "No deliveries, pickups or new loads yesterday. Tap to see what's on today."
    revenue = sum((l.price for l in delivered), Decimal(0))
    parts = [f"{len(delivered)} delivered" + (f" ({_money(revenue)})" if delivered else ""),
             f"{picked} picked up", f"{booked} booked"]
    return "Yesterday: " + ", ".join(parts) + ". Tap for the full report."


def send_sunrise(force=False):
    """Send today's Sunrise notification to every staff subscription that hasn't had it yet.

    Run by a systemd timer at 6 AM. Outside the morning window it does nothing (so a server that was off at
    6 doesn't send yesterday's news at 3 PM), unless force=True.
    """
    if not enabled():
        return {"skipped": "VAPID keys not set"}
    now = local_now()
    hour = current_app.config["SUNRISE_HOUR"]
    if not force and not (hour <= now.hour < hour + 3):
        return {"skipped": f"outside the {hour}:00 window"}
    today = now.date()
    yesterday = today - timedelta(days=1)
    subs = (PushSubscription.query.join(User).filter(User.is_active_flag.is_(True))
            .filter((PushSubscription.last_sent_on.is_(None)) | (PushSubscription.last_sent_on < today)).all())
    counts = {"ok": 0, "gone": 0, "error": 0}
    bodies = {}
    for sub in subs:
        if not sub.user.is_staff:
            continue
        if sub.company_id not in bodies:
            bodies[sub.company_id] = sunrise_summary(sub.company_id, yesterday)
        result = send(sub, {"title": "☀️ Sunrise report", "body": bodies[sub.company_id],
                            "url": "/reports/sunrise", "tag": "sunrise-" + today.isoformat()})
        counts[result] += 1
        if result == "ok":
            sub.last_sent_on = today
    db.session.commit()
    return counts
