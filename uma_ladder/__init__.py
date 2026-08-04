from __future__ import annotations

import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import BaseConfig, get_config
from .extensions import csrf, db, limiter, login_manager, migrate


def create_app(config_object: type[BaseConfig] | str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    if config_object is None:
        config_object = get_config(os.environ.get("FLASK_CONFIG"))
    elif isinstance(config_object, str):
        config_object = get_config(config_object)
    app.config.from_object(config_object)

    # PR-Q1 — Initialize Sentry as early as possible so any errors
    # during the rest of startup get captured. No-op when SENTRY_DSN
    # is unset (the dev / test default).
    _init_sentry(app)

    # PR-K2.2 — Fly's edge proxy terminates TLS and forwards to the
    # app over plaintext HTTP, setting `X-Forwarded-Proto: https`.
    # Without ProxyFix, `request.scheme` reads `http` and any
    # `url_for(_external=True)` emits an `http://` URL — broke the
    # Discord OAuth `redirect_uri` byte-exact match on first deploy.
    # Trust exactly one hop (Fly's edge); don't widen unless we add
    # another proxy in front.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    _enable_sqlite_foreign_keys(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    csrf.init_app(app)
    # PR-Q2 — rate-limiter. Disabled in tests so the suite doesn't 429
    # itself; in dev / prod the per-route `@limiter.limit(...)`
    # decorators activate. RATELIMIT_ENABLED is the official toggle
    # flask-limiter reads off `app.config`.
    app.config.setdefault(
        "RATELIMIT_ENABLED", not app.config.get("TESTING", False)
    )
    limiter.init_app(app)
    _register_rate_limit_error_handler(app)
    _warn_if_tailwind_built_but_missing(app)

    from . import models  # noqa: F401 — register mappers for Alembic + tests
    from .models import User

    @login_manager.user_loader
    def _load_user(user_id: str) -> User | None:
        return db.session.get(User, int(user_id))

    _register_blueprints(app)
    _register_health(app)
    _register_inbox_context(app)
    _register_template_filters(app)

    from .cli import register_cli

    register_cli(app)

    return app


def _register_blueprints(app: Flask) -> None:
    from .admin.routes import bp as admin_bp
    from .api.routes import bp as api_bp
    from .auth.routes import bp as auth_bp
    from .clubs.routes import bp as clubs_bp
    from .dashboard.routes import bp as dashboard_bp
    from .draft.routes import bp as draft_bp
    from .inbox.routes import bp as inbox_bp
    from .notifications.routes import bp as notifications_bp
    from .ocr.routes import bp as ocr_bp
    from .official.routes import bp as official_bp
    from .presets.routes import bp as presets_bp
    from .profiles.routes import bp as profiles_bp
    from .rankings.routes import bp as rankings_bp
    from .skills.routes import bp as skills_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(profiles_bp, url_prefix="/profiles")
    app.register_blueprint(clubs_bp, url_prefix="/clubs")
    app.register_blueprint(rankings_bp, url_prefix="/rankings")
    app.register_blueprint(official_bp, url_prefix="/official")
    app.register_blueprint(draft_bp, url_prefix="/draft")
    app.register_blueprint(presets_bp, url_prefix="/presets")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(inbox_bp, url_prefix="/inbox")
    app.register_blueprint(ocr_bp, url_prefix="/ocr")
    app.register_blueprint(skills_bp, url_prefix="/skills")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    # Machine-facing, bearer-token authenticated (uma-race-extract).
    # CSRF-exempt on purpose: CSRF defends cookie-based auth, which a
    # browser attaches automatically. A bearer token never is, so the
    # attack it protects against cannot happen here — and requiring a
    # CSRF token would make the endpoint unusable from a desktop tool.
    csrf.exempt(api_bp)
    app.register_blueprint(api_bp, url_prefix="/api")


def _init_sentry(app: Flask) -> None:
    """PR-Q1 — initialize Sentry when SENTRY_DSN is configured.

    Hard-deps `sentry-sdk[flask]` (declared in pyproject) so the
    import is unconditional; the no-op behaviour comes from
    skipping `init` entirely when the DSN is absent. That keeps
    dev / test environments cleanly off Sentry without any
    plumbing on each developer's machine.

    Wiring choices:
    - `FlaskIntegration` — captures unhandled exceptions, request
      context, route info.
    - `LoggingIntegration` — promotes `app.logger.warning` and
      higher to Sentry events; lower levels become breadcrumbs
      attached to subsequent events. So a degraded path that
      logs a warning before throwing surfaces both the warning
      AND the exception together.
    - `traces_sample_rate=0.0` — performance monitoring is off
      for now (errors only). Bump to e.g. 0.1 (sample 10% of
      requests) once we're sure the cost is bounded.
    - `send_default_pii=False` — keeps user IPs / emails / etc.
      out of Sentry payloads. We can opt in selectively via
      `sentry_sdk.set_user()` if a future error handler wants
      to attach the logged-in user id.

    Init runs in `create_app` so tests that build multiple apps
    will repeatedly call `sentry_sdk.init` — that's a global no-op
    in dev/test (DSN unset) and harmless even if a test leaks a
    DSN env var in (Sentry treats repeated init as re-config).
    """
    dsn = app.config.get("SENTRY_DSN")
    if not dsn:
        return

    import logging

    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            FlaskIntegration(),
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.WARNING,
            ),
        ],
        environment=app.config.get("SENTRY_ENVIRONMENT") or "production",
        traces_sample_rate=0.0,
        send_default_pii=False,
    )


def _register_rate_limit_error_handler(app: Flask) -> None:
    """Render a friendly 429 instead of flask-limiter's bare text
    body. The handler runs both for the synthetic per-route limits
    (PR-Q2) and any future global limits."""
    from flask import render_template

    @app.errorhandler(429)
    def _too_many_requests(error):  # noqa: ANN001 — Flask error handler shape
        # `error.description` is flask-limiter's "N per HOUR"
        # string; surfacing it lets the user understand what they
        # tripped without having to inspect Retry-After headers.
        retry_after = getattr(error, "description", None)
        return render_template(
            "errors/429.html",
            retry_after=retry_after,
        ), 429


def _register_template_filters(app: Flask) -> None:
    """PR-Q3a — single Jinja filter so historical-context templates
    render user names consistently. Active accounts get
    display_name (fallback to username), soft-deleted accounts get
    the `K***************e` partial mask. Templates use
    `{{ user | masked_display }}` instead of inlining the
    display_name-or-username fallback (which would leak the raw
    handle for any soft-deleted user)."""
    from .services.profiles import masked_display_for
    from .services.stat_ranks import (
        aptitude_grade_icon_filename,
        rank_points_icon_filename,
        rank_points_label,
        season_icon_filename,
        stat_rank_icon_filename,
        stat_rank_label,
        weather_icon_filename,
    )

    app.jinja_env.filters["masked_display"] = masked_display_for
    # PR-A2 — stat-rank helpers exposed as Jinja filters so the
    # per-result stat grid can do `{{ value | stat_rank_label }}`
    # and `{{ value | stat_rank_icon_filename }}` without import
    # gymnastics in templates.
    app.jinja_env.filters["stat_rank_label"] = stat_rank_label
    app.jinja_env.filters["stat_rank_icon_filename"] = stat_rank_icon_filename
    # PR-A3 — aptitude grade icon (G/F/E/D/C/B/A/S → 00..07.png).
    app.jinja_env.filters["aptitude_grade_icon_filename"] = (
        aptitude_grade_icon_filename
    )
    # PR-A4 — season + weather text/icon glyphs.
    app.jinja_env.filters["weather_icon_filename"] = weather_icon_filename
    app.jinja_env.filters["season_icon_filename"] = season_icon_filename
    # PR-A6 — overall uma rank from uma_score.
    app.jinja_env.filters["rank_points_label"] = rank_points_label
    app.jinja_env.filters["rank_points_icon_filename"] = rank_points_icon_filename
    # PR-OCR21 — aptitude → stat modifiers. Registered as a global
    # rather than a filter because it takes two args (the result + the
    # race) and `{{ result | effective_stats(race) }}` reads worse
    # than `{% set eff = effective_stats(r, race) %}` at the call site.
    from .services.aptitude_stats import effective_stats_for_result

    app.jinja_env.globals["effective_stats"] = effective_stats_for_result


def _register_inbox_context(app: Flask) -> None:
    """PR-J12 — make `unread_inbox_count` available to every
    template so the navbar bell badge can render on any page
    without each route having to remember to pass it in.

    Costs one cheap COUNT query per page render for authenticated
    users — covered by the user_id+read_at composite index. For
    anonymous visitors the function returns 0 immediately.
    """
    from flask_login import current_user

    @app.context_processor
    def _inject_unread_inbox_count() -> dict[str, int]:
        if not current_user.is_authenticated:
            return {"unread_inbox_count": 0}
        from .services import inbox as inbox_service

        return {
            "unread_inbox_count": inbox_service.unread_count_for_user(
                current_user.id
            )
        }


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
