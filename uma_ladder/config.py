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
    DISCORD_WEBHOOK_RACE_REGISTRATION_URL: str | None = None
    DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL: str | None = None
    DISCORD_WEBHOOK_DRAFT_RESULTS_URL: str | None = None
    DISCORD_WEBHOOK_FALLBACK_URL: str | None = None
    OCR_PROVIDER: str = "manual"
    GOOGLE_VISION_API_KEY: str | None = None
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
    DISCORD_WEBHOOK_FALLBACK_URL = _env_or_none("DISCORD_WEBHOOK_FALLBACK_URL")
    OCR_PROVIDER = os.environ.get("OCR_PROVIDER", "manual")
    GOOGLE_VISION_API_KEY = _env_or_none("GOOGLE_VISION_API_KEY")
    TAILWIND_BUILT = os.environ.get("TAILWIND_BUILT", "").lower() in ("1", "true", "yes")


class TestConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class ProdConfig(BaseConfig):
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
        cls.DISCORD_WEBHOOK_FALLBACK_URL = _env_or_none("DISCORD_WEBHOOK_FALLBACK_URL")
        cls.OCR_PROVIDER = os.environ.get("OCR_PROVIDER", "manual")
        cls.GOOGLE_VISION_API_KEY = _env_or_none("GOOGLE_VISION_API_KEY")
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
