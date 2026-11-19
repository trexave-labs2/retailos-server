# server/app/config.py
import os


def normalize_database_url(database_url):
    if not database_url:
        return database_url

    if database_url.startswith("postgresql://"):
        return database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1
        )

    if database_url.startswith("postgres://"):
        return database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1
        )

    return database_url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = normalize_database_url(
        os.environ.get(
            "DATABASE_URL",
            "sqlite:///store.db"
        )
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.environ.get(
        "SESSION_COOKIE_SECURE",
        "false"
    ).lower() == "true"
    SESSION_COOKIE_SAMESITE = os.environ.get(
        "SESSION_COOKIE_SAMESITE",
        "Lax"
    )
    SESSION_COOKIE_PARTITIONED = os.environ.get(
        "SESSION_COOKIE_PARTITIONED",
        "false"
    ).lower() == "true"
    FRONTEND_URL = os.environ.get(
        "FRONTEND_URL",
        "http://localhost:3000"
    ).rstrip("/")
    TESTING = False
    CORS_ORIGINS = os.environ.get(
        "CORS_ORIGINS",
        FRONTEND_URL
    )
    PLATFORM_ADMIN_EMAIL = os.environ.get("PLATFORM_ADMIN_EMAIL", "").strip().lower()
    PLATFORM_ADMIN_PASSWORD = os.environ.get("PLATFORM_ADMIN_PASSWORD", "")
    PLATFORM_ADMIN_BOOTSTRAP_EMAIL = os.environ.get("PLATFORM_ADMIN_BOOTSTRAP_EMAIL")
    PLATFORM_ADMIN_BOOTSTRAP_TOKEN = os.environ.get("PLATFORM_ADMIN_BOOTSTRAP_TOKEN")
    PASSWORD_RESET_SMTP_HOST = os.environ.get("PASSWORD_RESET_SMTP_HOST", "")
    PASSWORD_RESET_SMTP_PORT = int(os.environ.get("PASSWORD_RESET_SMTP_PORT", "587"))
    PASSWORD_RESET_SMTP_USE_TLS = os.environ.get("PASSWORD_RESET_SMTP_USE_TLS", "true").lower() == "true"
    PASSWORD_RESET_SMTP_USERNAME = os.environ.get("PASSWORD_RESET_SMTP_USERNAME", "")
    PASSWORD_RESET_SMTP_PASSWORD = os.environ.get("PASSWORD_RESET_SMTP_PASSWORD", "")
    PASSWORD_RESET_SMTP_TIMEOUT_SECONDS = float(os.environ.get("PASSWORD_RESET_SMTP_TIMEOUT_SECONDS", "10"))
    PASSWORD_RESET_FROM_EMAIL = os.environ.get("PASSWORD_RESET_FROM_EMAIL", "")
    PASSWORD_RESET_TOKEN_TTL_MINUTES = int(os.environ.get("PASSWORD_RESET_TOKEN_TTL_MINUTES", "30"))


class ProductionConfig(Config):
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "None"
    SESSION_COOKIE_PARTITIONED = True
    # RATELIMIT_STORAGE_URI is deliberately not read here: a class attribute is
    # evaluated once at import time. create_app() reads and validates it instead.


class TestConfig(Config):
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    TESTING = True
    RATELIMIT_ENABLED = False
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_SAMESITE = "Lax"
    FRONTEND_URL = "http://localhost:3000"
    CORS_ORIGINS = "http://localhost:3000"
