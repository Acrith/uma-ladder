from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
# PR-Q2 — per-IP rate limiter. `get_remote_address` reads from
# `request.remote_addr`, which is already corrected by ProxyFix
# (PR-K2.2) so it reflects the real client IP behind Fly's edge.
# In-memory storage is fine for a single-process Fly machine; when
# we scale to multiple workers we'll swap to Redis or memcached
# via `RATELIMIT_STORAGE_URI`. No default global limits — each
# sensitive route opts in via `@limiter.limit(...)`.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)
