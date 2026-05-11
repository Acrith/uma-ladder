from __future__ import annotations

import hmac

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import limiter
from ..services import auth as auth_service
from ..services import auth_identities as identity_service
from ..services import oauth as oauth_service
from ..services.redirects import safe_redirect_target
from .forms import LoginForm, RegisterForm, RequestResetForm, ResetPasswordForm

bp = Blueprint("auth", __name__, template_folder="templates")


# ─── Provider-agnostic OAuth helpers ─────────────────────────────
#
# Per-provider state/verifier session keys + config lookup keys, so
# adding a new provider is "register a row in this table + drop in
# routes that pass the name" rather than copying the whole helper
# stack. Routes still live at /auth/<provider>/{start,callback,unlink}
# (separate registrations so each provider gets a redirect URI
# under its own path — required by every OAuth registration UI).


_PROVIDER_FACTORIES: dict[
    str, type[oauth_service.OAuthProvider]
] = {
    "discord": oauth_service.DiscordOAuthProvider,
    "google": oauth_service.GoogleOAuthProvider,
}


def _state_key(provider: str) -> str:
    return f"oauth_{provider}_state"


def _verifier_key(provider: str) -> str:
    return f"oauth_{provider}_verifier"


def _config_keys(provider: str) -> tuple[str, str, str]:
    """Returns (client_id_key, client_secret_key, redirect_uri_key)
    for ``current_app.config`` lookups."""
    p = provider.upper()
    return (
        f"{p}_OAUTH_CLIENT_ID",
        f"{p}_OAUTH_CLIENT_SECRET",
        f"{p}_OAUTH_REDIRECT_URI",
    )


def _oauth_configured(provider: str) -> bool:
    id_key, secret_key, _ = _config_keys(provider)
    cfg = current_app.config
    return bool(cfg.get(id_key) and cfg.get(secret_key))


def _oauth_redirect_uri(provider: str) -> str:
    """Where the provider sends the user after consent.

    Honours an explicit ``<PROVIDER>_OAUTH_REDIRECT_URI`` override —
    needed if the Fly proxy ever misreports scheme/host — and
    otherwise builds it from ``url_for(..., _external=True)`` which
    inherits ``APP_BASE_URL`` in production.
    """
    _, _, redirect_key = _config_keys(provider)
    override = current_app.config.get(redirect_key)
    if override:
        return override
    return url_for(f"auth.{provider}_callback", _external=True)


def _build_provider(provider: str) -> oauth_service.OAuthProvider:
    cls = _PROVIDER_FACTORIES[provider]
    id_key, secret_key, _ = _config_keys(provider)
    cfg = current_app.config
    transport = current_app.extensions.get(
        "uma_ladder.oauth_http_transport"
    ) or oauth_service.UrllibHttpTransport()
    return cls(
        client_id=cfg[id_key],
        client_secret=cfg[secret_key],
        transport=transport,
    )


@bp.get("/")
def index() -> str:
    return "auth placeholder"


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register() -> object:
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = RegisterForm()
    if form.validate_on_submit():
        try:
            user = auth_service.register_user(
                auth_service.RegistrationRequest(
                    username=form.username.data or "",
                    password=form.password.data or "",
                )
            )
        except auth_service.UsernameTakenError:
            form.username.errors.append("That username is already taken.")
        except auth_service.AuthError as exc:
            form.username.errors.append(str(exc))
        else:
            login_user(user)
            return redirect(url_for("dashboard.index"))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per 15 minutes", methods=["POST"])
def login() -> object:
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = auth_service.authenticate(
                form.username.data or "", form.password.data or ""
            )
        except auth_service.RateLimitedError as exc:
            # PR-J10 — surface a specific cooldown message rather
            # than the generic "invalid". Usernames are public via
            # /profiles already, so disclosing "this account is
            # locked" doesn't leak new information.
            mins = max(1, (exc.retry_after_seconds + 59) // 60)
            form.password.errors.append(
                f"Too many failed attempts. Try again in {mins} minute(s)."
            )
        except auth_service.InvalidCredentialsError:
            form.password.errors.append("Invalid username or password.")
        except auth_service.InactiveUserError:
            form.username.errors.append("Account is disabled.")
        else:
            login_user(user)
            # PR-J8 — open-redirect gate. `next` is attacker-controllable
            # via crafted login URLs; only accept same-origin relative
            # paths.
            next_url = safe_redirect_target(
                request.args.get("next"),
                default=url_for("dashboard.index"),
            )
            return redirect(next_url)
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout() -> object:
    logout_user()
    return redirect(url_for("dashboard.index"))


@bp.route("/reset", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def request_reset() -> object:
    form = RequestResetForm()
    issued_token: str | None = None
    if form.validate_on_submit():
        user = auth_service.find_user_by_username(form.username.data or "")
        if user is not None and user.is_active:
            issued_token = auth_service.issue_reset_token(
                current_app.config["SECRET_KEY"], user
            )
        flash("If that account exists, a reset link has been generated.")
    return render_template(
        "auth/request_reset.html", form=form, issued_token=issued_token
    )


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token: str) -> object:
    try:
        user = auth_service.consume_reset_token(
            current_app.config["SECRET_KEY"], token
        )
    except auth_service.InvalidResetTokenError:
        return render_template("auth/reset_invalid.html"), 400
    form = ResetPasswordForm()
    if form.validate_on_submit():
        auth_service.set_password(user, form.password.data or "")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form)


# ─── OAuth login + link (Discord, Google) ────────────────────────


_PROVIDER_LABELS: dict[str, str] = {
    "discord": "Discord",
    "google": "Google",
}


def _oauth_start(provider: str) -> object:
    """Kick off the OAuth dance.

    When ``current_user.is_authenticated``: this is a "link my
    <provider>" intent — the callback attaches the verified identity
    to the logged-in account.

    When anonymous: this is a "log in with <provider>" intent — the
    callback either logs the user in (existing identity) or
    creates a new account and logs them in.

    The branch is inferred at the callback from the live session
    rather than encoded in state, so a stale start URL can't be
    weaponised across sessions.
    """
    if not _oauth_configured(provider):
        abort(404)

    state = oauth_service.generate_state()
    verifier, challenge = oauth_service.generate_pkce_pair()
    session[_state_key(provider)] = state
    session[_verifier_key(provider)] = verifier

    auth_url = _build_provider(provider).authorization_url(
        state=state,
        code_challenge=challenge,
        redirect_uri=_oauth_redirect_uri(provider),
    )
    return redirect(auth_url)


def _oauth_callback(provider: str) -> object:
    if not _oauth_configured(provider):
        abort(404)

    label = _PROVIDER_LABELS.get(provider, provider.title())

    # Always pop the session secrets even on early-exit paths so a
    # cancelled flow can't leave stale state hanging around for a
    # later replay.
    expected_state = session.pop(_state_key(provider), None)
    code_verifier = session.pop(_verifier_key(provider), None)

    provider_error = request.args.get("error")
    if provider_error:
        flash(f"{label} sign-in cancelled ({provider_error}).")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or not expected_state or not code_verifier:
        flash(f"{label} sign-in failed: missing or expired state.")
        return redirect(url_for("auth.login"))

    # Constant-time compare guards against any future timing-oracle
    # gotcha; the values are server-generated tokens so equality is
    # purely a CSRF check, not a secret-recovery surface.
    if not hmac.compare_digest(state, expected_state):
        abort(400)

    try:
        profile = _build_provider(provider).exchange_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=_oauth_redirect_uri(provider),
        )
    except oauth_service.OAuthExchangeError as exc:
        current_app.logger.warning(
            "%s oauth exchange failed: %s", provider, exc
        )
        flash(f"{label} sign-in failed. Please try again.")
        return redirect(url_for("auth.login"))

    existing = identity_service.find_identity(
        profile.provider, profile.external_id
    )

    # ── Link mode: user is already signed in ──
    if current_user.is_authenticated:
        if existing is not None:
            if existing.user_id == current_user.id:
                flash(f"Your {label} account is already linked.")
            else:
                flash(
                    f"That {label} account is already linked to a different "
                    "Uma Ladder user."
                )
            return redirect(
                url_for("profiles.public", username=current_user.username)
            )
        try:
            identity_service.link_identity(current_user, profile)
        except identity_service.IdentityAlreadyLinkedError:
            # Race window between the find above and the insert; rare
            # but worth handling cleanly rather than 500'ing.
            flash(
                f"That {label} account is already linked to a different "
                "Uma Ladder user."
            )
            return redirect(
                url_for("profiles.public", username=current_user.username)
            )
        flash(f"{label} account linked.")
        return redirect(
            url_for("profiles.public", username=current_user.username)
        )

    # ── Login mode: anonymous visitor ──
    if existing is not None:
        identity_service.touch_login(existing)
        login_user(existing.user)
        return redirect(url_for("dashboard.index"))

    # No identity yet → create a new local user from the OAuth
    # profile and log them in. Username is derived from the provider
    # display name with suffix-on-collision; the new account has an
    # unguessable random password, so admin-issued reset is the
    # only password-side recovery path.
    try:
        user = identity_service.create_user_for_oauth(profile)
    except identity_service.UsernameDerivationError:
        current_app.logger.warning(
            "could not derive unique username for %s %s",
            provider,
            profile.external_id,
        )
        flash(
            f"Could not create an account from your {label} profile. "
            "Please contact an admin."
        )
        return redirect(url_for("auth.login"))

    login_user(user)
    flash(f"Welcome, {user.username}! Account created via {label}.")
    return redirect(url_for("dashboard.index"))


# ─── Discord routes ──────────────────────────────────────────────


@bp.get("/discord/start")
def discord_start() -> object:
    return _oauth_start("discord")


@bp.get("/discord/callback")
@limiter.limit("10 per hour")
def discord_callback() -> object:
    return _oauth_callback("discord")


@bp.post("/discord/unlink")
@login_required
def discord_unlink() -> object:
    """Detach the user's Discord identity.

    Always clears `UserProfile.discord_user_id` too: that field is
    overwritten by every OAuth link, so post-unlink the snowflake
    on the profile is whatever OAuth set last — not whatever the
    user originally typed (we don't track origin). Better to clear
    it and let them re-enter manually if they want pings without
    the identity link.

    `discord_handle` is left alone — it's display-only and may
    have been their original entry from before OAuth was wired.
    """
    from ..services import profiles as profiles_service

    removed = identity_service.unlink_identity(current_user, "discord")
    if removed:
        profile = profiles_service.get_or_create_profile(current_user)
        profile.discord_user_id = None
        from ..extensions import db

        db.session.commit()
        flash("Discord account unlinked.")
    else:
        # Idempotent — clicking unlink twice shouldn't error.
        flash("No Discord account was linked.")
    return redirect(url_for("profiles.me"))


# ─── Google routes ───────────────────────────────────────────────


@bp.get("/google/start")
def google_start() -> object:
    return _oauth_start("google")


@bp.get("/google/callback")
@limiter.limit("10 per hour")
def google_callback() -> object:
    return _oauth_callback("google")


@bp.post("/google/unlink")
@login_required
def google_unlink() -> object:
    """Detach the user's Google identity.

    No mirror cleanup needed — Google linking doesn't write to any
    UserProfile column today (we only requested ``openid profile``,
    no email/handle to mirror). The auth_identities row going away
    is the entire effect.
    """
    removed = identity_service.unlink_identity(current_user, "google")
    if removed:
        flash("Google account unlinked.")
    else:
        flash("No Google account was linked.")
    return redirect(url_for("profiles.me"))
