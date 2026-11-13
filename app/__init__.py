import os

from dotenv import load_dotenv
from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import CONFIGS
from .extensions import csrf, db, login_manager


def create_app(env=None):
    load_dotenv()
    env = env or os.environ.get("APP_ENV", "production")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIGS[env])

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is not set. Copy .env.example to .env and fill it in.")

    os.makedirs(app.instance_path, exist_ok=True)

    # Behind Cloudflare Tunnel / nginx: trust one proxy hop for scheme + client IP
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None

    from .models import User

    @login_manager.user_loader
    def load_user(token):
        try:
            uid, ver = token.split(":")
            user = db.session.get(User, int(uid))
        except (ValueError, TypeError):
            return None
        if user and user.is_active and str(user.session_version) == ver:
            return user
        return None

    from .blueprints import auth, main, loads, inspections, customers, expenses, team, trucks, reports, settings
    for bp in (auth.bp, main.bp, loads.bp, inspections.bp, customers.bp, expenses.bp,
               team.bp, trucks.bp, reports.bp, settings.bp):
        app.register_blueprint(bp)

    from . import filters
    filters.register(app)

    from . import cli
    cli.register(app)

    @app.context_processor
    def inject_globals():
        ua = request.headers.get("User-Agent", "")
        return {
            "APP_NAME": app.config["APP_NAME"],
            "IN_ANDROID_APP": "VTMApp/" in ua,
        }

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(self)")
        return resp

    for code in (403, 404, 413, 500):
        app.register_error_handler(code, _error_page(code))

    return app


def _error_page(code):
    messages = {
        403: "You don't have access to that page.",
        404: "That page doesn't exist (or you don't have access to it).",
        413: "That upload is too large. Try fewer photos at a time.",
        500: "Something went wrong on our side. Please try again.",
    }

    def handler(_e):
        return render_template("errors/error.html", code=code, message=messages[code]), code
    return handler
