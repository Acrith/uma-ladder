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
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    csrf.init_app(app)

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


def _register_health(app: Flask) -> None:
    @app.get("/healthz")
    def healthz() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200
