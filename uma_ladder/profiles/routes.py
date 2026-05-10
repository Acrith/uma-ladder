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
from ..services import auth_identities as identity_service
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
    linked_identities = identity_service.list_identities_for_user(current_user)
    discord_identity = next(
        (i for i in linked_identities if i.provider == "discord"), None
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
        # PR-K3.1 — `discord_user_id` is locked when Discord is OAuth-
        # linked. Disabled inputs don't submit, so a normal save would
        # otherwise clear the verified mirror; an attacker could
        # re-enable the field via DevTools and inject any snowflake
        # (e.g. spoof @-mentions to someone else's Discord). Server-
        # side: always preserve the existing value when linked,
        # ignoring whatever the form supplies.
        if discord_identity is not None:
            discord_user_id_for_update = profile.discord_user_id
        else:
            discord_user_id_for_update = form.discord_user_id.data or None
        update = profiles_service.ProfileUpdate(
            display_name=form.display_name.data or None,
            avatar_url=avatar_url,
            description=form.description.data or None,
            friend_code=form.friend_code.data or None,
            discord_handle=form.discord_handle.data or None,
            discord_user_id=discord_user_id_for_update,
            oshi_character_id=form.oshi_character_id.data or None,
            oshi_outfit_id=outfit_id,
            avatar_border=form.avatar_border.data or None,
        )
        try:
            profiles_service.update_profile(current_user, update)
        except profiles_service.UnknownOshiError:
            form.oshi_character_id.errors.append("Unknown character.")
        except profiles_service.UnknownOutfitError as exc:
            form.oshi_character_id.errors.append(f"Outfit: {exc}")
        except profiles_service.UnknownAvatarBorderError:
            form.avatar_border.errors.append("Pick a tone from the list.")
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
        linked_identities=linked_identities,
        discord_identity=discord_identity,
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


def _load_profile_or_404(username: str):
    """PR-P3 / PR-P3.1 — shared loader for the three profile tab
    routes.

    Resolves the user + profile + EVERYTHING the shared hero
    needs (identity, oshi, stat tiles, achievement count for the
    tab badge, discord-verified flag, season standing, Elo, track
    strengths). The hero renders on every tab so all this data
    must be loaded for every route. Tab-specific data is fetched
    per-route below.
    """
    from ..services import achievements as achievements_service
    from ..services import draft as draft_service
    from ..services import official as official_service
    from ..services import seasons as seasons_service
    from ..services import uma_moe as uma_moe_service

    user = profiles_service.find_user_by_username(username)
    if user is None:
        abort(404)
    profile = profiles_service.get_or_create_profile(user)

    # Hero data — every tab needs these for the persistent banner.
    active_season = seasons_service.get_active_season()
    standing = (
        official_service.season_standing_for_user(user.id, active_season.id)
        if active_season is not None
        else None
    )
    # Discord-verified status drives the chip glyph in the hero
    # contact line. Cheap query, OK on every tab.
    discord_verified = (
        identity_service.find_identity("discord", profile.discord_user_id)
        is not None
        if profile.discord_user_id
        else False
    )
    # Trainer summary is needed for sync_club_id_from_trainer
    # (keeps club_id mirror fresh) AND for the In-game stats
    # card on Overview. Trainer is loaded here even though
    # only Overview uses it — the side-effect (club_id sync)
    # benefits any tab visit.
    trainer = uma_moe_service.fetch_trainer_summary(profile.friend_code)
    profiles_service.sync_club_id_from_trainer(profile, trainer)

    return {
        "user": user,
        "profile": profile,
        "oshi_image": profiles_service.resolve_oshi_image(profile),
        "achievement_count": len(
            achievements_service.list_for_user(user.id)
        ),
        "active_season": active_season,
        "standing": standing,
        "track_strengths": profiles_service.track_strengths_for_user(user.id),
        "elo": draft_service.elo_summary_for_user(user.id),
        "trainer": trainer,
        "discord_verified": discord_verified,
    }


@bp.get("/<username>")
def public(username: str) -> object:
    """Overview tab — shared hero (loaded by _load_profile_or_404)
    + the in-game stats card from uma.moe + a top-5 achievements
    snippet. Match history + full achievements catalogue live on
    their own tabs (PR-P3)."""
    base = _load_profile_or_404(username)
    user = base["user"]
    from ..services import achievements as achievements_service

    overview_achievements = achievements_service.list_for_user(user.id)[:5]
    return render_template(
        "profiles/public.html",
        overview_achievements=overview_achievements,
        **base,
    )


@bp.get("/<username>/history")
def public_history(username: str) -> object:
    """Match history tab — Recent Official + Recent Draft +
    most-used uma + per-track strengths. The shared loader
    already provides track_strengths + elo (the hero needs them);
    only the match-history-specific data is fetched here."""
    base = _load_profile_or_404(username)
    user = base["user"]
    page = max(1, request.args.get("page", 1, type=int))
    kind = (request.args.get("kind") or "").strip() or None
    history = profiles_service.list_recent_history_for_user(
        user.id, page=page, page_size=10, kind=kind
    )
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
    return render_template(
        "profiles/public_history.html",
        history=history,
        recent_official=recent_official,
        recent_draft=recent_draft,
        most_used_official=most_used_official,
        most_used_draft=most_used_draft,
        **base,
    )


@bp.get("/<username>/achievements")
def public_achievements(username: str) -> object:
    """Achievements tab — full catalogue with locked entries
    grayed-out + how-to-earn tooltips. Revises PR-P2's
    "show only unlocked" rule for this dedicated surface;
    Overview tab still shows unlocked-only as a snippet."""
    from ..services import achievements as achievements_service

    base = _load_profile_or_404(username)
    user = base["user"]
    catalogue = achievements_service.list_definitions()
    user_grants = achievements_service.list_for_user(user.id)
    granted_by_id = {ua.achievement_id: ua for ua in user_grants}
    # Group by source_kind so the catalogue reads coherently
    # rather than as one flat list. Order: manual / oauth_link /
    # race_result / draft_match / season_close / (None bucket).
    group_order = [
        ("manual", "Special"),
        ("race_result", "Official races"),
        ("draft_match", "Draft matches"),
        ("season_close", "Season"),
        ("oauth_link", "Sign-in"),
        (None, "Other"),
    ]
    grouped: dict[str | None, list] = {k: [] for k, _ in group_order}
    for a in catalogue:
        bucket = a.source_kind if a.source_kind in grouped else None
        grouped[bucket].append(a)
    return render_template(
        "profiles/public_achievements.html",
        grouped_achievements=[
            (label, grouped[k]) for k, label in group_order if grouped[k]
        ],
        granted_by_id=granted_by_id,
        unlocked_count=len(user_grants),
        total_count=len(catalogue),
        **base,
    )
