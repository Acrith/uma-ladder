from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import UploadedImage
from ..models.enums import UploadPurpose
from ..services import ocr as ocr_service
from ..services import profiles as profiles_service
from .forms import ProfileForm

bp = Blueprint("profiles", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    """Public players browse — paginated list with username search."""
    page = max(1, request.args.get("page", 1, type=int))
    q = (request.args.get("q") or "").strip()
    players = profiles_service.list_players(q=q, page=page, page_size=30)
    return render_template("profiles/index.html", players=players, q=q)


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
        # Uploaded avatar wins over the URL field — handles the common
        # case of a user pasting a URL once, then later uploading their
        # own image (the upload is the more deliberate action).
        avatar_url = form.avatar_url.data or None
        if form.avatar_image.data:
            try:
                avatar_url = profiles_service.save_avatar_upload(
                    form.avatar_image.data, user=current_user
                )
            except ocr_service.OcrError as exc:
                flash(f"Avatar upload failed: {exc}")
                return redirect(url_for("profiles.me"))
        update = profiles_service.ProfileUpdate(
            display_name=form.display_name.data or None,
            avatar_url=avatar_url,
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


@bp.post("/me/avatar/remove")
@login_required
def remove_avatar() -> object:
    """Clear the user's avatar_url. The underlying UploadedImage row is
    kept so storage usage stays small enough that we don't need a GC
    pass — restorable by pasting a previous /profiles/avatars/<id> URL."""
    profiles_service.clear_avatar(current_user)
    flash("Avatar removed.")
    return redirect(url_for("profiles.me"))


@bp.get("/avatars/<int:image_id>")
def serve_avatar(image_id: int) -> object:
    """Public serve endpoint for user-uploaded avatars. No auth — avatars
    are part of public profiles. Refuses to serve images uploaded for any
    other purpose, so the OCR upload bucket isn't accidentally exposed
    by guessing image_ids."""
    image = db.session.get(UploadedImage, image_id)
    if image is None or image.purpose != UploadPurpose.AVATAR:
        abort(404)
    directory = ocr_service.image_path(image).parent
    return send_from_directory(directory, image.storage_key)


@bp.get("/<username>")
def public(username: str) -> object:
    from ..services import draft as draft_service
    from ..services import uma_moe as uma_moe_service

    user = profiles_service.find_user_by_username(username)
    if user is None:
        abort(404)
    profile = profiles_service.get_or_create_profile(user)
    page = max(1, request.args.get("page", 1, type=int))
    kind = (request.args.get("kind") or "").strip() or None
    # Combined paginated view (URL escape hatch via ?page= / ?kind= —
    # not surfaced in the new layout but still usable directly).
    history = profiles_service.list_recent_history_for_user(
        user.id, page=page, page_size=10, kind=kind
    )
    # Split top-5 lists for the side-by-side dashboard cards. Cheap —
    # same merge cost twice with kind filter applied per call.
    recent_official = profiles_service.list_recent_history_for_user(
        user.id, page=1, page_size=5, kind="official"
    )
    recent_draft = profiles_service.list_recent_history_for_user(
        user.id, page=1, page_size=5, kind="draft"
    )
    most_used_official = profiles_service.most_used_umas_for_user(
        user.id, kind="official", limit=5
    )
    most_used_draft = profiles_service.most_used_umas_for_user(
        user.id, kind="draft", limit=5
    )
    elo = draft_service.elo_summary_for_user(user.id)
    # Best-effort uma.moe enrichment when friend_code is set. Returns
    # None for missing code / 404 / network error / malformed JSON —
    # the template just doesn't render the card in that case.
    trainer = uma_moe_service.fetch_trainer_summary(profile.friend_code)
    return render_template(
        "profiles/public.html",
        user=user,
        profile=profile,
        oshi_image=profiles_service.resolve_oshi_image(profile),
        history=history,
        recent_official=recent_official,
        recent_draft=recent_draft,
        most_used_official=most_used_official,
        most_used_draft=most_used_draft,
        elo=elo,
        trainer=trainer,
    )
