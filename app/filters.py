from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .helpers import money
from .models import STATUS_LABELS

STATUS_COLORS = {
    "quote": "secondary",
    "booked": "info",
    "assigned": "primary",
    "picked_up": "warning",
    "delivered": "success",
    "paid": "dark",
    "cancelled": "light",
}


def local(v):
    """DB stores naive UTC; show it in the company's time zone."""
    from flask import current_app
    tz = ZoneInfo(current_app.config["APP_TIMEZONE"])
    return v.replace(tzinfo=timezone.utc).astimezone(tz)


def register(app):
    app.jinja_env.filters["money"] = money

    @app.template_filter("d")
    def fmt_date(v):
        if not v:
            return "—"
        if isinstance(v, datetime):
            v = local(v)
            return v.strftime("%b %-d, %Y %-I:%M %p")
        if isinstance(v, date):
            return v.strftime("%b %-d, %Y")
        return str(v)

    @app.template_filter("short_date")
    def short_date(v):
        return v.strftime("%b %-d") if v else "—"

    @app.template_filter("iso")
    def iso(v):
        return v.isoformat() if v else ""

    @app.template_filter("status_badge")
    def status_badge(status):
        from markupsafe import Markup, escape
        color = STATUS_COLORS.get(status, "secondary")
        text_cls = " text-dark border" if color in ("light", "warning", "info") else ""
        return Markup(f'<span class="badge bg-{color}{text_cls}">{escape(STATUS_LABELS.get(status, status))}</span>')

    @app.template_filter("tel")
    def tel(v):
        return "".join(ch for ch in (v or "") if ch.isdigit() or ch == "+")

    @app.template_filter("maps")
    def maps(parts):
        from urllib.parse import quote_plus
        q = ", ".join(p for p in parts if p)
        return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(q) if q else None
