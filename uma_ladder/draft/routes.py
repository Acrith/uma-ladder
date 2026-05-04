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

from ..models import DraftBanType, DraftMatchStatus
from ..models.enums import VENUES, Direction, DistanceCategory, Surface
from ..services import draft as draft_service
from ..services import profiles as profiles_service
from ..services import seasons as seasons_service
from ..services.profiles import (
    list_enabled_characters,
    list_outfits_for_character,
)
from ..services.randomizer import RandomizerError
from .forms import CreateDraftForm, CsrfOnlyForm, JoinDraftForm, RoomCodeForm

bp = Blueprint("draft", __name__, template_folder="templates")


# Static option lists for track-ban "Value" select. Direction intentionally
# restricts to Left/Right — these are the values that affect skill aptitudes.
# Straight and Stretch exist in the enum so existing race presets parse, but
# the lone Stretch course (Niigata 1000m) isn't a useful ban target.
TRACK_BAN_OPTIONS: dict[str, list[str]] = {
    "venue": list(VENUES),
    "direction": [Direction.LEFT.value, Direction.RIGHT.value],
    "distance_category": [d.value for d in DistanceCategory],
    "surface": [s.value for s in Surface],
}


@bp.get("/")
def index() -> object:
    matches = []
    if current_user.is_authenticated:
        matches = draft_service.list_matches_for_user(current_user.id)
    return render_template(
        "draft/index.html", matches=matches, join_form=JoinDraftForm()
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new() -> object:
    form = CreateDraftForm()
    season = seasons_service.get_active_season()
    if not season:
        flash("No active season; ask an admin to create one.")
        return redirect(url_for("draft.index"))
    if form.validate_on_submit():
        try:
            match = draft_service.create_match(
                draft_service.CreateMatchRequest(
                    season_id=season.id,
                    host_user_id=current_user.id,
                    umas_per_player=int(form.umas_per_player.data or 2),
                    preset_pool=form.preset_pool.data or "custom",
                )
            )
        except draft_service.DraftError as exc:
            flash(str(exc))
        else:
            return redirect(url_for("draft.detail", match_id=match.id))
    return render_template("draft/new.html", form=form)


@bp.post("/join")
@login_required
def join_by_code() -> object:
    form = JoinDraftForm()
    if not form.validate_on_submit():
        flash("Enter a join code.")
        return redirect(url_for("draft.index"))
    match = draft_service.find_by_join_code(form.join_code.data or "")
    if match is None:
        flash("No match with that code.")
        return redirect(url_for("draft.index"))
    try:
        draft_service.join_match(match.id, current_user.id)
    except draft_service.CannotJoinOwnMatchError:
        flash("You cannot join your own match.")
    except draft_service.MatchFullError:
        flash("That match is full.")
    except draft_service.InvalidMatchStateError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match.id))


@bp.get("/<int:match_id>")
@login_required
def detail(match_id: int) -> object:
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)
    bans = draft_service.list_bans(match_id)
    should_poll = _should_poll(match, bans)
    # During the track-ban phase, prune options that would empty the pool
    # given the opponent's existing ban + cross-category dependencies.
    if match.status == DraftMatchStatus.TRACK_BAN_PHASE:
        track_ban_options = draft_service.feasible_track_ban_options(
            match_id,
            current_user.id,
            static_options=TRACK_BAN_OPTIONS,
        )
    else:
        track_ban_options = TRACK_BAN_OPTIONS

    # Per-player panel context — profile + resolved Oshi image so the duel
    # layout can render each side as a Live Preview-style card.
    sides = []
    side_meta = [
        ("host", "Host", match.host, match.host_user_id, match.host_ready),
        (
            "opponent",
            "Opponent",
            match.opponent,
            match.opponent_user_id,
            match.opponent_ready,
        ),
    ]
    for key, label, user, user_id, ready in side_meta:
        profile = (
            profiles_service.get_or_create_profile(user) if user else None
        )
        oshi_image = (
            profiles_service.resolve_oshi_image(profile) if profile else None
        )
        player_bans = [b for b in bans if user_id is not None and b.user_id == user_id]
        sides.append(
            {
                "key": key,
                "label": label,
                "user": user,
                "user_id": user_id,
                "profile": profile,
                "oshi_image": oshi_image,
                "ready": ready,
                "bans": player_bans,
                "is_me": user_id == current_user.id if user_id else False,
            }
        )

    return render_template(
        "draft/detail.html",
        match=match,
        bans=bans,
        sides=sides,
        room_code_form=RoomCodeForm(),
        csrf_form=CsrfOnlyForm(),
        room_code_expired=draft_service.is_room_code_expired(match),
        characters=list_enabled_characters(),
        track_ban_options=track_ban_options,
        should_poll=should_poll,
    )


def _should_poll(match, bans) -> bool:
    """Only poll when the user has nothing to fill out — otherwise the swap
    nukes mid-typing values. After the user submits their pending action,
    polling resumes so they see the opponent's action land."""
    status = match.status
    if status == DraftMatchStatus.WAITING_FOR_OPPONENT:
        return True
    me = current_user.id
    if status == DraftMatchStatus.READY_CHECK:
        if me == match.host_user_id:
            return bool(match.host_ready)
        if me == match.opponent_user_id:
            return bool(match.opponent_ready)
        return False
    if status == DraftMatchStatus.TRACK_BAN_PHASE:
        return any(
            b.user_id == me and b.ban_type in {
                DraftBanType.VENUE,
                DraftBanType.DIRECTION,
                DraftBanType.DISTANCE_CATEGORY,
                DraftBanType.SURFACE,
            }
            for b in bans
        )
    if status == DraftMatchStatus.UMA_BAN_PHASE:
        return any(
            b.user_id == me and b.ban_type == DraftBanType.UMA for b in bans
        )
    return False


# ---------- HTMX partials ----------


@bp.get("/<int:match_id>/_partials/uma-ban-outfits")
@login_required
def partial_uma_ban_outfits(match_id: int) -> object:
    """Outfit dropdown for a chosen character on the uma-ban form.

    Includes an "(any outfit)" option that records uma_outfit_id=null on
    the ban — the result-validation treats that as a whole-character ban.
    """
    raw = (request.args.get("uma_character_id") or "").strip()
    char_id = int(raw) if raw.isdigit() else None
    outfits = list_outfits_for_character(char_id) if char_id else []
    return render_template(
        "draft/_partial_uma_ban_outfits.html", outfits=outfits
    )


@bp.get("/<int:match_id>/_partials/result-outfits")
@login_required
def partial_result_outfits(match_id: int) -> object:
    """Outfit dropdown on the result-submission form, scoped to one player."""
    participant = (request.args.get("participant") or "").strip()
    raw = (request.args.get("uma_character_id_" + participant) or "").strip()
    char_id = int(raw) if raw.isdigit() else None
    outfits = list_outfits_for_character(char_id) if char_id else []
    return render_template(
        "draft/_partial_result_outfits.html",
        outfits=outfits,
        participant=participant,
    )


# ---------- Action handlers ----------


@bp.post("/<int:match_id>/ready")
@login_required
def ready(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.ready_up(match_id, current_user.id)
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/track-ban")
@login_required
def track_ban(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    ban_type = (request.form.get("ban_type") or "").strip()
    raw_condition = (request.form.get("condition_key") or "").strip()
    # The form posts every per-type select; pick the one matching ban_type.
    typed_value = (request.form.get(f"condition_key_{ban_type}") or "").strip()
    condition = typed_value or raw_condition
    try:
        draft_service.submit_track_ban(
            match_id,
            current_user.id,
            ban_type=ban_type,
            condition_key=condition,
        )
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/randomize")
@login_required
def randomize(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.randomize_preset(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)
    except RandomizerError as exc:
        flash(f"No preset matched the bans: {exc.reason}")
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/skip-track-ban")
@login_required
def skip_track_ban(match_id: int) -> object:
    """Allow a player to skip their track ban (no condition picked)."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.submit_track_ban(
            match_id,
            current_user.id,
            ban_type="venue",
            condition_key="__skip__",
        )
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/uma-ban")
@login_required
def uma_ban(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    char_raw = request.form.get("uma_character_id", "").strip()
    outfit_raw = request.form.get("uma_outfit_id", "").strip()
    if not char_raw.isdigit():
        flash("Pick a character.")
        return redirect(url_for("draft.detail", match_id=match_id))
    outfit_id = int(outfit_raw) if outfit_raw.isdigit() else None
    try:
        draft_service.submit_uma_ban(
            match_id,
            current_user.id,
            int(char_raw),
            uma_outfit_id=outfit_id,
        )
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/room-code")
@login_required
def room_code(match_id: int) -> object:
    form = RoomCodeForm()
    if not form.validate_on_submit():
        flash("Enter a room code.")
        return redirect(url_for("draft.detail", match_id=match_id))
    try:
        draft_service.set_room_code(match_id, form.room_code.data or "")
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/results")
@login_required
def submit_results(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)

    lines: list[draft_service.DraftResultLine] = []
    for participant in (match.host_user_id, match.opponent_user_id):
        if participant is None:
            continue
        placement_raw = request.form.get(f"placement_{participant}", "").strip()
        if not placement_raw.isdigit():
            flash("Enter a placement for every player.")
            return redirect(url_for("draft.detail", match_id=match_id))
        char_raw = request.form.get(f"uma_character_id_{participant}", "").strip()
        outfit_raw = request.form.get(f"uma_outfit_id_{participant}", "").strip()
        char_id = int(char_raw) if char_raw.isdigit() else None
        outfit_id = int(outfit_raw) if outfit_raw.isdigit() else None
        custom = (
            request.form.get(f"custom_uma_name_{participant}", "").strip() or None
        )
        lines.append(
            draft_service.DraftResultLine(
                user_id=participant,
                placement=int(placement_raw),
                uma_character_id=char_id,
                uma_outfit_id=outfit_id,
                custom_uma_name=custom,
            )
        )
    try:
        draft_service.submit_results(
            match_id, lines, confirmed_by_user_id=current_user.id
        )
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.get("/ladder/<int:season_id>")
def ladder(season_id: int) -> object:
    rows = draft_service.season_elo_ladder(season_id)
    return render_template("draft/ladder.html", season_id=season_id, rows=rows)
