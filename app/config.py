"""All configuration comes from environment variables (.env in production).

Nothing secret is ever hard-coded here. See .env.example for the full list.
"""
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "vtm.sqlite3")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # Uploads (inspection photos, signatures, receipts) live OUTSIDE /static
    # and are only served to logged-in users of the same company.
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "instance", "uploads"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "60")) * 1024 * 1024
    PHOTO_MAX_PX = int(os.environ.get("PHOTO_MAX_PX", "2000"))

    PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.environ.get("SESSION_DAYS", "30")))
    REMEMBER_COOKIE_DURATION = PERMANENT_SESSION_LIFETIME
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("COOKIE_SECURE", True)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True

    WTF_CSRF_TIME_LIMIT = None  # tie CSRF token to session, not a clock

    APP_NAME = os.environ.get("APP_NAME", "Vehicle Transport Manager")
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/New_York")
    APP_URL = os.environ.get("APP_URL", "https://vehicletransport.autos")

    # Android app distribution (served from /app)
    ANDROID_APK_FILE = os.environ.get("ANDROID_APK_FILE", "vtm.apk")
    ANDROID_VERSION_CODE = int(os.environ.get("ANDROID_VERSION_CODE", "1"))
    ANDROID_VERSION_NAME = os.environ.get("ANDROID_VERSION_NAME", "1.0.0")

    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "8"))
    LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "15"))


class DevConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    SECRET_KEY = Config.SECRET_KEY or "dev-only-not-secret"


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


CONFIGS = {"production": Config, "development": DevConfig, "testing": TestConfig}
