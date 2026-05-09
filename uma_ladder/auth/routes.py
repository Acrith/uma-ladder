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

from ..services import auth as auth_service
from ..services import auth_identities as identity_service
from ..services import oauth as oauth_service
from ..services.redirects import safe_redirect_target
from .forms import LoginForm, RegisterForm, RequestResetForm, ResetPasswordForm

bp = Blueprint("auth", __name__, template_folder="templates")


# ─── Discord OAuth helpers ───────────────────────────────────────


_DISCORD_STATE_KEY = "oauth_discord_state"
_DISCORD_VERIFIER_KEY = "oauth_discord_verifier"


def _discord_oauth_configured() -> bool:
    cfg = current_app.config
    return bool(
        cfg.get("DISCORD_OAUTH_CLIENT_ID")
        and cfg.get("DISCORD_OAUTH_CLIENT_SECRET")
    )


def _discord_redirect_uri() -> str:
    """Where Discord sends the user after consent.

    Honours an explicit ``DISCORD_OAUTH_REDIRECT_URI`` override —
    needed if the Fly proxy ever misreports scheme/host — and
    otherwise builds it from ``url_for(..., _external=True)`` which
    inherits ``APP_BASE_URL`` in production.
    """
    override = current_app.config.get("DISCORD_OAUTH_REDIRECT_URI")
    if override:
        return override
    return url_for("auth.discord_callback", _external=True)


def _discord_provider() -> oauth_service.DiscordOAuthProvider:
    cfg = current_app.config
    transport = current_app.extensions.get(
        "uma_ladder.oauth_http_transport"
    ) or oauth_service.UrllibHttpTransport()
    return oauth_service.DiscordOAuthProvider(
        client_id=cfg["DISCORD_OAUTH_CLIENT_ID"],
        client_secret=cfg["DISCORD_OAUTH_CLIENT_SECRET"],
        transport=transport,
    )


@bp.get("/")
def index() -> str:
    return "auth placeholder"


@bp.route("/register", methods=["GET", "POST"])
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


# ─── Discord OAuth login + link ──────────────────────────────────


@bp.get("/discord/start")
def discord_start() -> object:
    """Kick off the OAuth dance.

    When ``current_user.is_authenticated``: this is a "link my
    Discord" intent — the callback attaches the verified identity
    to the logged-in account.

    When anonymous: this is a "log in with Discord" intent — the
    callback either logs the user in (existing identity) or
    creates a new account and logs them in.

    The branch is inferred at the callback from the live session
    rather than encoded in state, so a stale start URL can't be
    weaponised across sessions.
    """
    if not _discord_oauth_configured():
        abort(404)

    state = oauth_service.generate_state()
    verifier, challenge = oauth_service.generate_pkce_pair()
    session[_DISCORD_STATE_KEY] = state
    session[_DISCORD_VERIFIER_KEY] = verifier

    auth_url = _discord_provider().authorization_url(
        state=state,
        code_challenge=challenge,
        redirect_uri=_discord_redirect_uri(),
    )
    return redirect(auth_url)


@bp.get("/discord/callback")
def discord_callback() -> object:
    if not _discord_oauth_configured():
        abort(404)

    # Always pop the session secrets even on early-exit paths so a
    # cancelled flow can't leave stale state hanging around for a
    # later replay.
    expected_state = session.pop(_DISCORD_STATE_KEY, None)
    code_verifier = session.pop(_DISCORD_VERIFIER_KEY, None)

    discord_error = request.args.get("error")
    if discord_error:
        flash(f"Discord sign-in cancelled ({discord_error}).")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or not expected_state or not code_verifier:
        flash("Discord sign-in failed: missing or expired state.")
        return redirect(url_for("auth.login"))

    # Constant-time compare guards against any future timing-oracle
    # gotcha; the values are server-generated tokens so equality is
    # purely a CSRF check, not a secret-recovery surface.
    if not hmac.compare_digest(state, expected_state):
        abort(400)

    try:
        profile = _discord_provider().exchange_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=_discord_redirect_uri(),
        )
    except oauth_service.OAuthExchangeError as exc:
        current_app.logger.warning("discord oauth exchange failed: %s", exc)
        flash("Discord sign-in failed. Please try again.")
        return redirect(url_for("auth.login"))

    existing = identity_service.find_identity(
        profile.provider, profile.external_id
    )

    # ── Link mode: user is already signed in ──
    if current_user.is_authenticated:
        if existing is not None:
            if existing.user_id == current_user.id:
                flash("Your Discord account is already linked.")
            else:
                flash(
                    "That Discord account is already linked to a different "
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
                "That Discord account is already linked to a different "
                "Uma Ladder user."
            )
            return redirect(
                url_for("profiles.public", username=current_user.username)
            )
        flash("Discord account linked.")
        return redirect(
            url_for("profiles.public", username=current_user.username)
        )

    # ── Login mode: anonymous visitor ──
    if existing is not None:
        identity_service.touch_login(existing)
        login_user(existing.user)
        return redirect(url_for("dashboard.index"))

    # No identity yet → create a new local user from the Discord
    # profile and log them in. Username is derived from the Discord
    # display name with suffix-on-collision; the new account has an
    # unguessable random password, so admin-issued reset is the
    # only password-side recovery path.
    try:
        user = identity_service.create_user_for_oauth(profile)
    except identity_service.UsernameDerivationError:
        current_app.logger.warning(
            "could not derive unique username for discord %s",
            profile.external_id,
        )
        flash(
            "Could not create an account from your Discord profile. "
            "Please contact an admin."
        )
        return redirect(url_for("auth.login"))

    login_user(user)
    flash(f"Welcome, {user.username}! Account created via Discord.")
    return redirect(url_for("dashboard.index"))
