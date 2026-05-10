from __future__ import annotations

import os


class ConfigError(RuntimeError):
    pass


class BaseConfig:
    SECRET_KEY: str = ""
    SQLALCHEMY_DATABASE_URI: str = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    TESTING = False
    DEBUG = False
    # PR-J8 — cap request body at the WSGI layer. The OCR upload
    # path also enforces 8 MiB per-file post-save (services/ocr.py),
    # but without this Werkzeug streams the entire body to disk
    # first; an unbounded POST is a disk-fill DoS. 16 MiB leaves
    # headroom for legitimate multi-screenshot batches under the
    # 8 MiB-per-file ceiling.
    MAX_CONTENT_LENGTH: int = 16 * 1024 * 1024
    DISCORD_WEBHOOK_RACE_REGISTRATION_URL: str | None = None
    DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL: str | None = None
    DISCORD_WEBHOOK_DRAFT_RESULTS_URL: str | None = None
    DISCORD_WEBHOOK_ADMIN_AUDIT_URL: str | None = None
    DISCORD_WEBHOOK_FALLBACK_URL: str | None = None
    # PR-K1/K2 — Discord OAuth login. Soft-required: when any of
    # these is unset the /auth/discord routes 404 and the login
    # page hides the "Continue with Discord" button. App boot
    # never fails on missing OAuth config so dev environments
    # without a Discord app stay usable.
    DISCORD_OAUTH_CLIENT_ID: str | None = None
    DISCORD_OAUTH_CLIENT_SECRET: str | None = None
    # Optional explicit redirect URI override. When unset the route
    # builds it from `url_for(..., _external=True)` which honours
    # APP_BASE_URL. Set this if Fly's reverse proxy ever lies about
    # scheme/host.
    DISCORD_OAUTH_REDIRECT_URI: str | None = None
    # PR-K4 — Google OAuth login. Same soft-required shape as
    # Discord above.
    GOOGLE_OAUTH_CLIENT_ID: str | None = None
    GOOGLE_OAUTH_CLIENT_SECRET: str | None = None
    GOOGLE_OAUTH_REDIRECT_URI: str | None = None
    OCR_PROVIDER: str = "manual"
    GOOGLE_VISION_API_KEY: str | None = None
    # Public base URL for outgoing links (Discord embed titles point
    # back to /official/<id> etc.). Empty / unset = no link added,
    # embed still posts. Avoid a trailing slash.
    APP_BASE_URL: str | None = None
    UMA_MOE_BASE_URL: str = "https://uma.moe"
    UMA_MOE_CACHE_TTL_HOURS: int = 12
    # Optional API key for uma.moe — currently powers usage tracking
    # only; the upstream operator has signalled it'll become required
    # at some future cutover. Send via `X-API-Key` header when set.
    UMA_MOE_API_KEY: str | None = None
    # Set to True in production where Tailwind has been built into
    # uma_ladder/static/css/output.css. Dev defaults to False so the CDN
    # fallback in base.html avoids a build step on every reload.
    TAILWIND_BUILT: bool = False


def _env_or_none(key: str) -> str | None:
    val = os.environ.get(key, "")
    return val or None


class DevConfig(BaseConfig):
    DEBUG = True
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///uma_ladder.dev.sqlite"
    )
    DISCORD_WEBHOOK_RACE_REGISTRATION_URL = _env_or_none("DISCORD_WEBHOOK_RACE_REGISTRATION_URL")
    DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL = _env_or_none("DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL")
    DISCORD_WEBHOOK_DRAFT_RESULTS_URL = _env_or_none("DISCORD_WEBHOOK_DRAFT_RESULTS_URL")
    DISCORD_WEBHOOK_ADMIN_AUDIT_URL = _env_or_none("DISCORD_WEBHOOK_ADMIN_AUDIT_URL")
    DISCORD_WEBHOOK_FALLBACK_URL = _env_or_none("DISCORD_WEBHOOK_FALLBACK_URL")
    DISCORD_OAUTH_CLIENT_ID = _env_or_none("DISCORD_OAUTH_CLIENT_ID")
    DISCORD_OAUTH_CLIENT_SECRET = _env_or_none("DISCORD_OAUTH_CLIENT_SECRET")
    DISCORD_OAUTH_REDIRECT_URI = _env_or_none("DISCORD_OAUTH_REDIRECT_URI")
    GOOGLE_OAUTH_CLIENT_ID = _env_or_none("GOOGLE_OAUTH_CLIENT_ID")
    GOOGLE_OAUTH_CLIENT_SECRET = _env_or_none("GOOGLE_OAUTH_CLIENT_SECRET")
    GOOGLE_OAUTH_REDIRECT_URI = _env_or_none("GOOGLE_OAUTH_REDIRECT_URI")
    OCR_PROVIDER = os.environ.get("OCR_PROVIDER", "manual")
    GOOGLE_VISION_API_KEY = _env_or_none("GOOGLE_VISION_API_KEY")
    APP_BASE_URL = _env_or_none("APP_BASE_URL")
    UMA_MOE_API_KEY = _env_or_none("UMA_MOE_API_KEY")
    TAILWIND_BUILT = os.environ.get("TAILWIND_BUILT", "").lower() in ("1", "true", "yes")


class TestConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class ProdConfig(BaseConfig):
    # PR-J8 — cookie hardening. Fly serves over HTTPS-only, so
    # SECURE is safe to require. SAMESITE=Lax keeps top-level
    # navigations (post-login redirects, OAuth-style links) working
    # while blocking cross-site POSTs from carrying the cookie.
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    REMEMBER_COOKIE_SECURE: bool = True
    REMEMBER_COOKIE_HTTPONLY: bool = True
    REMEMBER_COOKIE_SAMESITE: str = "Lax"

    @classmethod
    def _load(cls) -> type[BaseConfig]:
        secret = os.environ.get("SECRET_KEY")
        if not secret:
            raise ConfigError("SECRET_KEY is required in production")
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise ConfigError("DATABASE_URL is required in production")
        cls.SECRET_KEY = secret
        cls.SQLALCHEMY_DATABASE_URI = db_url
        cls.DISCORD_WEBHOOK_RACE_REGISTRATION_URL = _env_or_none(
            "DISCORD_WEBHOOK_RACE_REGISTRATION_URL"
        )
        cls.DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL = _env_or_none(
            "DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL"
        )
        cls.DISCORD_WEBHOOK_DRAFT_RESULTS_URL = _env_or_none(
            "DISCORD_WEBHOOK_DRAFT_RESULTS_URL"
        )
        cls.DISCORD_WEBHOOK_ADMIN_AUDIT_URL = _env_or_none(
            "DISCORD_WEBHOOK_ADMIN_AUDIT_URL"
        )
        cls.DISCORD_WEBHOOK_FALLBACK_URL = _env_or_none("DISCORD_WEBHOOK_FALLBACK_URL")
        cls.DISCORD_OAUTH_CLIENT_ID = _env_or_none("DISCORD_OAUTH_CLIENT_ID")
        cls.DISCORD_OAUTH_CLIENT_SECRET = _env_or_none(
            "DISCORD_OAUTH_CLIENT_SECRET"
        )
        cls.DISCORD_OAUTH_REDIRECT_URI = _env_or_none(
            "DISCORD_OAUTH_REDIRECT_URI"
        )
        cls.GOOGLE_OAUTH_CLIENT_ID = _env_or_none("GOOGLE_OAUTH_CLIENT_ID")
        cls.GOOGLE_OAUTH_CLIENT_SECRET = _env_or_none(
            "GOOGLE_OAUTH_CLIENT_SECRET"
        )
        cls.GOOGLE_OAUTH_REDIRECT_URI = _env_or_none(
            "GOOGLE_OAUTH_REDIRECT_URI"
        )
        cls.OCR_PROVIDER = os.environ.get("OCR_PROVIDER", "manual")
        cls.GOOGLE_VISION_API_KEY = _env_or_none("GOOGLE_VISION_API_KEY")
        cls.APP_BASE_URL = _env_or_none("APP_BASE_URL")
        cls.UMA_MOE_API_KEY = _env_or_none("UMA_MOE_API_KEY")
        cls.TAILWIND_BUILT = os.environ.get("TAILWIND_BUILT", "1").lower() in (
            "1",
            "true",
            "yes",
        )
        return cls


_REGISTRY: dict[str, type[BaseConfig]] = {
    "development": DevConfig,
    "dev": DevConfig,
    "testing": TestConfig,
    "test": TestConfig,
    "production": ProdConfig,
    "prod": ProdConfig,
}


def get_config(name: str | None) -> type[BaseConfig]:
    key = (name or "development").lower()
    try:
        cfg = _REGISTRY[key]
    except KeyError as exc:
        raise ConfigError(f"Unknown FLASK_CONFIG value: {name!r}") from exc
    if cfg is ProdConfig:
        return ProdConfig._load()
    return cfg
