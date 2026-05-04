from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
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
    outfits = (
        profiles_service.list_outfits_for_character(profile.oshi_character_id)
        if profile.oshi_character_id
        else []
    )
    if form.validate_on_submit():
        outfit_raw = (request.form.get("oshi_outfit_id") or "").strip()
        outfit_id = int(outfit_raw) if outfit_raw.isdigit() else None
        update = profiles_service.ProfileUpdate(
            display_name=form.display_name.data or None,
            avatar_url=form.avatar_url.data or None,
            description=form.description.data or None,
            friend_code=form.friend_code.data or None,
            discord_handle=form.discord_handle.data or None,
            oshi_character_id=form.oshi_character_id.data or None,
            oshi_outfit_id=outfit_id,
        )
        try:
            profiles_service.update_profile(current_user, update)
        except profiles_service.UnknownOshiError:
            form.oshi_character_id.errors.append("Unknown character.")
        except profiles_service.UnknownOutfitError as exc:
            form.oshi_character_id.errors.append(f"Outfit: {exc}")
        else:
            flash("Profile updated.")
            return redirect(url_for("profiles.me"))
    return render_template(
        "profiles/edit.html",
        form=form,
        profile=profile,
        characters=characters,
        outfits=outfits,
        oshi_image=profiles_service.resolve_oshi_image(profile),
    )


@bp.get("/_partials/outfits")
@login_required
def partial_outfits() -> object:
    """HTMX endpoint: outfit dropdown for the chosen character.

    Reads `oshi_character_id` from the query — that's the form field name,
    so `hx-include="this"` on the character `<select>` posts it directly
    without needing a `hx-vals` rename.
    """
    raw = (
        request.args.get("oshi_character_id")
        or request.args.get("character_id")
        or ""
    ).strip()
    selected_outfit = request.args.get("selected_outfit_id", "").strip()
    char_id = int(raw) if raw.isdigit() else None
    outfits = (
        profiles_service.list_outfits_for_character(char_id) if char_id else []
    )
    return render_template(
        "profiles/_partial_outfits.html",
        outfits=outfits,
        selected_outfit_id=int(selected_outfit) if selected_outfit.isdigit() else None,
    )


@bp.get("/<username>")
def public(username: str) -> object:
    user = profiles_service.find_user_by_username(username)
    if user is None:
        abort(404)
    profile = profiles_service.get_or_create_profile(user)
    return render_template(
        "profiles/public.html",
        user=user,
        profile=profile,
        oshi_image=profiles_service.resolve_oshi_image(profile),
    )
