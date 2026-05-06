from __future__ import annotations

import os

from flask import Flask

from .config import BaseConfig, get_config
from .extensions import csrf, db, login_manager, migrate


def create_app(config_object: type[BaseConfig] | str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    if config_object is None:
        config_object = get_config(os.environ.get("FLASK_CONFIG"))
    elif isinstance(config_object, str):
        config_object = get_config(config_object)
    app.config.from_object(config_object)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    _enable_sqlite_foreign_keys(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    csrf.init_app(app)
    _warn_if_tailwind_built_but_missing(app)

    from . import models  # noqa: F401 — register mappers for Alembic + tests
    from .models import User

    @login_manager.user_loader
    def _load_user(user_id: str) -> User | None:
        return db.session.get(User, int(user_id))

    _register_blueprints(app)
    _register_health(app)

    from .cli import register_cli

    register_cli(app)

    return app


def _register_blueprints(app: Flask) -> None:
    from .admin.routes import bp as admin_bp
    from .auth.routes import bp as auth_bp
    from .dashboard.routes import bp as dashboard_bp
    from .draft.routes import bp as draft_bp
    from .notifications.routes import bp as notifications_bp
    from .ocr.routes import bp as ocr_bp
    from .official.routes import bp as official_bp
    from .presets.routes import bp as presets_bp
    from .profiles.routes import bp as profiles_bp
    from .skills.routes import bp as skills_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(profiles_bp, url_prefix="/profiles")
    app.register_blueprint(official_bp, url_prefix="/official")
    app.register_blueprint(draft_bp, url_prefix="/draft")
    app.register_blueprint(presets_bp, url_prefix="/presets")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(ocr_bp, url_prefix="/ocr")
    app.register_blueprint(skills_bp, url_prefix="/skills")
    app.register_blueprint(admin_bp, url_prefix="/admin")


def _warn_if_tailwind_built_but_missing(app: Flask) -> None:
    """Belt-and-suspenders for the Docker build (PR-F3).

    When `TAILWIND_BUILT=1` is set we trust the Dockerfile's stage-1
    `make tailwind` to have produced
    `uma_ladder/static/css/output.css`. If that build silently
    failed (npm flake, config typo, network blip), the runtime
    flips to "expect built CSS" mode and the page loads with no
    styling — pages look like raw HTML and nothing in `flask run`
    or `gunicorn` complains.

    Log a loud error at startup so the next deploy line in `fly
    logs` makes the failure obvious. Doesn't refuse to boot — the
    app is still functional, just unstyled. CDN-fallback (DEBUG
    path) is unaffected: this only fires when TAILWIND_BUILT is on.
    """
    if not app.config.get("TAILWIND_BUILT"):
        return
    from pathlib import Path

    css_path = Path(app.static_folder) / "css" / "output.css"
    try:
        size = css_path.stat().st_size if css_path.exists() else 0
    except OSError:
        size = 0
    if size == 0:
        app.logger.error(
            "TAILWIND_BUILT=1 but %s is missing or empty — UI will be "
            "unstyled. Check the Dockerfile stage-1 build.",
            css_path,
        )


def _enable_sqlite_foreign_keys(app: Flask) -> None:
    """SQLite ships with FKs OFF by default — `ondelete='CASCADE'` is
    silently ignored unless we set `PRAGMA foreign_keys = ON` on every
    new connection. Postgres ignores this hook (DB-API doesn't expose
    `enable_load_extension`).

    Without this, deleting a parent row leaves orphan children behind
    instead of cascading — both in tests and production (Fly's volume
    runs SQLite). PR-G4 added this when implementing hard-delete.
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        if not hasattr(dbapi_connection, "execute"):
            return
        # Only sqlite3.Connection has `enable_load_extension`; using it
        # as a duck-type marker so the listener is a no-op for
        # Postgres / other engines.
        if not hasattr(dbapi_connection, "enable_load_extension"):
            return
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()


def _register_health(app: Flask) -> None:
    @app.get("/healthz")
    def healthz() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200
