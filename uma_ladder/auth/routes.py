from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..services import auth as auth_service
from ..services.redirects import safe_redirect_target
from .forms import LoginForm, RegisterForm, RequestResetForm, ResetPasswordForm

bp = Blueprint("auth", __name__, template_folder="templates")


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
