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

from ..extensions import db
from ..models import DraftBanType, DraftMatchStatus, OcrParseAttempt, UmaOutfit
from ..models.enums import VENUES, Direction, DistanceCategory, Surface
from ..services import draft as draft_service
from ..services import ocr as ocr_service
from ..services import profiles as profiles_service
from ..services import seasons as seasons_service
from ..services.permissions import min_role_required
from ..services.profiles import (
    list_enabled_characters,
    list_outfits_for_character,
)
from ..services.randomizer import RandomizerError
from .forms import (
    CreateDraftForm,
    CsrfOnlyForm,
    JoinDraftForm,
    ResultsScreenshotForm,
    RoomCodeForm,
)

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
    pending_invites = []
    if current_user.is_authenticated:
        matches = draft_service.list_matches_for_user(current_user.id)
        pending_invites = draft_service.list_pending_invites_for_user(
            current_user.id
        )
    return render_template(
        "draft/index.html",
        matches=matches,
        join_form=JoinDraftForm(),
        pending_invites=pending_invites,
        csrf_form=CsrfOnlyForm(),
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


@bp.post("/<int:match_id>/invite")
@login_required
def invite_player(match_id: int) -> object:
    """Layer-A invite (PR-I7) — host invites a specific user by
    username. The username field shares CSRF with the rest of the
    detail page; we just read request.form directly."""
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    username = (request.form.get("invitee_username") or "").strip()
    if not username:
        flash("Enter a username to invite.")
        return redirect(url_for("draft.detail", match_id=match_id))
    try:
        draft_service.invite_to_match(
            match_id,
            inviter_user_id=current_user.id,
            invitee_username=username,
        )
        flash(f"Invite sent to @{username}.")
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.InviteError as exc:
        flash(str(exc))
    except draft_service.InvalidMatchStateError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/invites/<int:invite_id>/accept")
@login_required
def accept_invite(invite_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    try:
        invite = draft_service.accept_invite(
            invite_id, by_user_id=current_user.id
        )
    except draft_service.InviteNotFoundError:
        abort(404)
    except draft_service.InviteError as exc:
        flash(str(exc))
        return redirect(url_for("draft.index"))
    except draft_service.DraftError as exc:
        flash(str(exc))
        return redirect(url_for("draft.index"))
    return redirect(url_for("draft.detail", match_id=invite.draft_match_id))


@bp.post("/invites/<int:invite_id>/decline")
@login_required
def decline_invite(invite_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    try:
        draft_service.decline_invite(invite_id, by_user_id=current_user.id)
        flash("Invite declined.")
    except draft_service.InviteNotFoundError:
        abort(404)
    except draft_service.InviteError as exc:
        flash(str(exc))
    return redirect(request.referrer or url_for("draft.index"))


@bp.post("/invites/<int:invite_id>/cancel")
@login_required
def cancel_invite(invite_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    try:
        invite = draft_service.cancel_invite(
            invite_id, by_user_id=current_user.id
        )
        flash("Invite cancelled.")
    except draft_service.InviteNotFoundError:
        abort(404)
    except draft_service.InviteError as exc:
        flash(str(exc))
        return redirect(request.referrer or url_for("draft.index"))
    return redirect(url_for("draft.detail", match_id=invite.draft_match_id))


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

    # Tile picker context for the uma-ban phase: every enabled outfit, plus
    # which ones are someone's Oshi (highlighted) and which are already
    # banned (grayed out / unselectable).
    all_outfits = profiles_service.list_all_outfits()
    oshi_outfit_ids = {
        s["profile"].oshi_outfit_id
        for s in sides
        if s["profile"] and s["profile"].oshi_outfit_id
    }
    banned_outfit_ids = {
        b.uma_outfit_id
        for b in bans
        if b.ban_type == DraftBanType.UMA and b.uma_outfit_id is not None
    }

    completed_results = (
        draft_service.list_results_for_match(match.id)
        if match.status == DraftMatchStatus.COMPLETED
        else []
    )
    completed_elo = (
        draft_service.list_elo_changes_for_match(match.id)
        if match.status == DraftMatchStatus.COMPLETED
        else []
    )
    # PR-J4 — surface the OCR source screenshots that seeded the
    # results so participants can verify what was parsed. Only
    # populated when results were submitted via the OCR flow;
    # manual-entry matches will return [].
    completed_screenshots = (
        ocr_service.get_draft_match_screenshots(match.id)
        if match.status == DraftMatchStatus.COMPLETED
        else []
    )
    outgoing_invites = (
        draft_service.list_outgoing_invites_for_match(match.id)
        if match.status == DraftMatchStatus.WAITING_FOR_OPPONENT
        else []
    )
    return render_template(
        "draft/detail.html",
        match=match,
        bans=bans,
        sides=sides,
        room_code_form=RoomCodeForm(),
        csrf_form=CsrfOnlyForm(),
        results_screenshot_form=ResultsScreenshotForm(),
        room_code_expired=draft_service.is_room_code_expired(match),
        characters=list_enabled_characters(),
        track_ban_options=track_ban_options,
        should_poll=should_poll,
        all_outfits=all_outfits,
        oshi_outfit_ids=oshi_outfit_ids,
        banned_outfit_ids=banned_outfit_ids,
        completed_results=completed_results,
        completed_elo=completed_elo,
        completed_screenshots=completed_screenshots,
        outgoing_invites=outgoing_invites,
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
    # Tile picker submits a single uma_outfit_id; derive char_id from the
    # outfit row so the service layer can keep its outfit-belongs-to-char
    # validation.
    outfit_raw = request.form.get("uma_outfit_id", "").strip()
    if not outfit_raw.isdigit():
        flash("Pick a costume tile.")
        return redirect(url_for("draft.detail", match_id=match_id))
    outfit_id = int(outfit_raw)
    outfit = db.session.get(UmaOutfit, outfit_id)
    if outfit is None:
        flash("Unknown costume.")
        return redirect(url_for("draft.detail", match_id=match_id))
    try:
        draft_service.submit_uma_ban(
            match_id,
            current_user.id,
            outfit.uma_character_id,
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
    except draft_service.BannedCharacterUsedError as exc:
        # Hint at the forfeit path. The standard /results route can't
        # silently apply forfeit because we don't know which player
        # picked the banned uma — leave it to the organiser to confirm.
        flash(
            f"{exc}. An organiser can mark the offending player as "
            "forfeit using the Forfeit form."
        )
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


# ---- OCR-driven results entry (PR-I1) ----


def _suggest_assignment(
    row: dict, host_username: str | None, opp_username: str | None
) -> str:
    """Given a parsed OCR row, decide whether the assign dropdown
    should default to 'host', 'opp', or 'skip'. Matches on the
    extracted `player_name` field; falls back to scanning both the
    cleaned `uma_name` and the unparsed `raw_uma_name` for a
    substring match (case-insensitive). The dual scan covers cases
    where structured extraction stripped the username out of
    uma_name into player_name (clean path) AND cases where
    extraction failed and the username only survives in the raw
    concat."""
    pname = (row.get("player_name") or "").strip().lower()
    fallback = " ".join(
        s.lower() for s in (
            row.get("uma_name") or "",
            row.get("raw_uma_name") or "",
        ) if s
    )
    for label, username in (("host", host_username), ("opp", opp_username)):
        if not username:
            continue
        u = username.lower()
        if pname == u:
            return label
        if u and u in fallback:
            return label
    return "skip"


def _user_can_submit_results(match) -> bool:
    """Anyone in the match (host or opponent) plus organizer+ may
    upload results screenshots. Same audience that can submit them
    manually via /results."""
    if not current_user.is_authenticated:
        return False
    if current_user.has_at_least("organizer"):
        return True
    return current_user.id in (match.host_user_id, match.opponent_user_id)


@bp.post("/<int:match_id>/results-screenshot")
@login_required
def upload_result_screenshot(match_id: int) -> object:
    """Step 1 of OCR-driven results: upload, parse, redirect to
    review.

    Accepts one OR multiple screenshots in a single submit. When the
    result screen scrolls past 9 entrants (rare on draft, common on
    larger official-style fields), the user can pick all the
    screenshots in one go and we merge the parsed rows by placement
    (highest-confidence wins on collisions).
    """
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)
    if not _user_can_submit_results(match):
        abort(403)
    form = ResultsScreenshotForm()
    if not form.validate_on_submit():
        for errs in form.errors.values():
            for err in errs:
                flash(err)
        return redirect(url_for("draft.detail", match_id=match_id))

    files = [f for f in request.files.getlist("image") if f and f.filename]
    if not files:
        flash("Pick at least one screenshot.")
        return redirect(url_for("draft.detail", match_id=match_id))

    attempts = []
    for f in files:
        try:
            image = ocr_service.save_uploaded_image(
                f, uploader_user_id=current_user.id
            )
        except ocr_service.OcrError as exc:
            flash(str(exc))
            return redirect(url_for("draft.detail", match_id=match_id))
        attempts.append(ocr_service.run_parse(image))

    # Single screenshot: keep the original behaviour — the attempt's
    # parsed rows already drive the review page.
    if len(attempts) == 1:
        return redirect(
            url_for(
                "draft.results_from_ocr",
                match_id=match_id,
                attempt_id=attempts[0].id,
            )
        )

    # Multi-screenshot: merge parsed rows into the FIRST attempt's
    # parsed_json so the review URL is unchanged. Dedupe by placement
    # (highest confidence wins); rows without a placement get
    # concatenated since they're informational only.
    by_placement: dict[int, dict] = {}
    unplaced: list[dict] = []
    for att in attempts:
        for row in (att.parsed_json or {}).get("rows", []) or []:
            p = row.get("placement")
            if p is None:
                unplaced.append(row)
                continue
            existing = by_placement.get(p)
            if existing is None or (
                row.get("confidence", 0) > existing.get("confidence", 0)
            ):
                by_placement[p] = row
    merged_rows = sorted(by_placement.values(), key=lambda r: r["placement"])
    merged_rows.extend(unplaced)
    primary = attempts[0]
    # Build the full dict before assigning so SQLAlchemy's change
    # tracker sees a single replacement on a plain JSON column.
    # In-place mutations after assignment aren't reliably persisted
    # without a MutableDict wrapper.
    new_parsed = dict(primary.parsed_json or {})
    new_parsed["rows"] = merged_rows
    new_parsed["screenshot_image_ids"] = [
        a.uploaded_image_id for a in attempts
    ]
    primary.parsed_json = new_parsed
    db.session.commit()
    return redirect(
        url_for(
            "draft.results_from_ocr",
            match_id=match_id,
            attempt_id=primary.id,
        )
    )


@bp.route("/<int:match_id>/results-from-ocr/<int:attempt_id>", methods=["GET", "POST"])
@login_required
def results_from_ocr(match_id: int, attempt_id: int) -> object:
    """Step 2: review parsed rows, assign each to host / opp / skip
    (bot), then submit. Multi-uma rows per player are supported —
    the placement-sum aggregation in submit_results (PR-I2) handles
    2v2/3v3 cleanly.
    """
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)
    if not _user_can_submit_results(match):
        abort(403)
    attempt = db.session.get(OcrParseAttempt, attempt_id)
    if attempt is None:
        abort(404)
    parsed_rows = (attempt.parsed_json or {}).get("rows", []) or []

    # Split rows so the review page leads with the ones that actually
    # represent race entrants. Vision picks up header chrome / button
    # labels / footer totals as their own clusters, and they all
    # arrive here without a placement digit. Hiding them under a
    # collapsible avoids drowning the user in 20 review rows when the
    # screen has 9 race entrants.
    placement_rows: list[dict] = []
    placement_row_indices: list[int] = []
    other_rows: list[dict] = []
    other_row_indices: list[int] = []
    for idx, row in enumerate(parsed_rows):
        if row.get("placement") is not None:
            placement_rows.append(row)
            placement_row_indices.append(idx)
        else:
            other_rows.append(row)
            other_row_indices.append(idx)

    if request.method == "POST":
        form = CsrfOnlyForm()
        if not form.validate_on_submit():
            abort(400)
        lines: list[draft_service.DraftResultLine] = []
        seen_placements: set[int] = set()
        for i, _row in enumerate(parsed_rows):
            assign = (request.form.get(f"assign_{i}") or "").strip()
            if assign == "skip" or assign == "":
                continue
            placement_raw = (request.form.get(f"placement_{i}") or "").strip()
            if not placement_raw.isdigit():
                flash("Each kept row needs a placement.")
                return redirect(
                    url_for(
                        "draft.results_from_ocr",
                        match_id=match_id,
                        attempt_id=attempt.id,
                    )
                )
            placement = int(placement_raw)
            if placement in seen_placements:
                flash(f"Duplicate placement {placement}.")
                return redirect(
                    url_for(
                        "draft.results_from_ocr",
                        match_id=match_id,
                        attempt_id=attempt.id,
                    )
                )
            seen_placements.add(placement)
            user_id_raw = (
                str(match.host_user_id)
                if assign == "host"
                else str(match.opponent_user_id)
            )
            if not user_id_raw or user_id_raw == "None":
                flash("Match has no opponent yet — can't assign results.")
                return redirect(url_for("draft.detail", match_id=match_id))
            uma_name = (request.form.get(f"uma_name_{i}") or "").strip() or None
            lines.append(
                draft_service.DraftResultLine(
                    user_id=int(user_id_raw),
                    placement=placement,
                    custom_uma_name=uma_name,
                )
            )
        if not lines:
            flash("No rows assigned to a player.")
            return redirect(
                url_for(
                    "draft.results_from_ocr",
                    match_id=match_id,
                    attempt_id=attempt.id,
                )
            )
        try:
            draft_service.submit_results(
                match_id, lines, confirmed_by_user_id=current_user.id
            )
            ocr_service.confirm_parse(
                attempt.id,
                confirmed_by_user_id=current_user.id,
                draft_match_id=match.id,
            )
        except draft_service.DraftError as exc:
            flash(str(exc))
            return redirect(
                url_for(
                    "draft.results_from_ocr",
                    match_id=match_id,
                    attempt_id=attempt.id,
                )
            )
        flash("Results saved.")
        return redirect(url_for("draft.detail", match_id=match_id))

    host_username = match.host.username if match.host else None
    opp_username = match.opponent.username if match.opponent else None
    suggested_assignments = {
        idx: _suggest_assignment(row, host_username, opp_username)
        for idx, row in zip(placement_row_indices, placement_rows, strict=True)
    }
    # Source-screenshot strip: the primary attempt has its own image,
    # plus parsed_json["screenshot_image_ids"] (set during multi-
    # screenshot upload) lists the full batch so we can show all of
    # them in the review preview, not just the first.
    from ..models import UploadedImage

    image_ids = (attempt.parsed_json or {}).get("screenshot_image_ids") or []
    if not image_ids:
        image_ids = [attempt.uploaded_image_id]
    source_images = [
        img for img in (db.session.get(UploadedImage, i) for i in image_ids)
        if img is not None
    ]
    return render_template(
        "draft/results_from_ocr.html",
        match=match,
        attempt=attempt,
        source_images=source_images,
        placement_rows=placement_rows,
        placement_row_indices=placement_row_indices,
        other_rows=other_rows,
        other_row_indices=other_row_indices,
        suggested_assignments=suggested_assignments,
        csrf_form=CsrfOnlyForm(),
    )


@bp.post("/<int:match_id>/forfeit")
@login_required
@min_role_required("organizer")
def forfeit(match_id: int) -> object:
    """Organiser closes a match by forfeit — the *other* player wins
    and Elo is applied. Used for ban violations + ghosting. The
    forfeiter id and the reason are persisted on the match."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    raw = (request.form.get("forfeiter_user_id") or "").strip()
    if not raw.isdigit():
        flash("Pick which player forfeits.")
        return redirect(url_for("draft.detail", match_id=match_id))
    reason = (request.form.get("reason") or "").strip() or None
    try:
        draft_service.submit_forfeit(
            match_id,
            forfeiter_user_id=int(raw),
            by_user_id=current_user.id,
            reason=reason,
        )
        flash("Forfeit recorded.")
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/cancel")
@login_required
@min_role_required("organizer")
def cancel(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.cancel_match(match_id, by_user_id=current_user.id)
        flash(f"Match #{match_id} cancelled.")
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/delete")
@login_required
@min_role_required("admin")
def delete(match_id: int) -> object:
    """Admin-only hard delete — cancel is the right tool 99% of the
    time; this exists for smoke-test cleanup."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.delete_match(match_id, by_user_id=current_user.id)
        flash(f"Match #{match_id} permanently deleted.")
    except draft_service.DraftNotFoundError:
        abort(404)
    return redirect(url_for("draft.index"))


@bp.get("/ladder/<int:season_id>")
def ladder(season_id: int) -> object:
    rows = draft_service.season_elo_ladder(season_id)
    return render_template("draft/ladder.html", season_id=season_id, rows=rows)
