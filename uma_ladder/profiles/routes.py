from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..services import profiles as profiles_service
from .forms import ProfileForm

bp = Blueprint("profiles", __name__, template_folder="templates")


@bp.get("/")
def index() -> str:
    return "profiles placeholder"


@bp.route("/me", methods=["GET", "POST"])
@login_required
def me() -> object:
    profile = profiles_service.get_or_create_profile(current_user)
    form = ProfileForm(obj=profile)
    characters = profiles_service.list_enabled_characters()
    if form.validate_on_submit():
        update = profiles_service.ProfileUpdate(
            display_name=form.display_name.data or None,
            avatar_url=form.avatar_url.data or None,
            description=form.description.data or None,
            friend_code=form.friend_code.data or None,
            discord_handle=form.discord_handle.data or None,
            oshi_character_id=form.oshi_character_id.data or None,
        )
        try:
            profiles_service.update_profile(current_user, update)
        except profiles_service.UnknownOshiError:
            form.oshi_character_id.errors.append("Unknown character.")
        else:
            flash("Profile updated.")
            return redirect(url_for("profiles.me"))
    return render_template(
        "profiles/edit.html", form=form, profile=profile, characters=characters
    )


@bp.get("/<username>")
def public(username: str) -> object:
    user = profiles_service.find_user_by_username(username)
    if user is None:
        abort(404)
    profile = profiles_service.get_or_create_profile(user)
    return render_template("profiles/public.html", user=user, profile=profile)
