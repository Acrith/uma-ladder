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

from ..extensions import db, limiter
from ..models import (
    OcrParseAttempt,
    OcrParseStatus,
    OfficialRaceRegistration,
    OfficialRaceResult,
    Role,
)
from ..services import ocr as ocr_service
from ..services import official as official_service
from ..services import presets as presets_service
from ..services import seasons as seasons_service
from ..services import track_conditions as track_conditions_service
from ..services.permissions import PermissionDeniedError, min_role_required
from .forms import (
    CreateOfficialRaceForm,
    CsrfOnlyForm,
    ResultsForm,
    ResultsScreenshotForm,
    RoomCodeForm,
)

bp = Blueprint("official", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    from sqlalchemy import select

    from ..models import (
        OfficialRace,
        OfficialRaceStatus,
        OfficialRaceVisibility,
        Season,
    )

    page = max(1, request.args.get("page", 1, type=int))
    status = (request.args.get("status") or "").strip()
    season_id_raw = (request.args.get("season_id") or "").strip()
    page_size = 50

    stmt = select(OfficialRace).order_by(OfficialRace.created_at.desc())
    if status:
        stmt = stmt.where(OfficialRace.status == status)
    if season_id_raw.isdigit():
        stmt = stmt.where(OfficialRace.season_id == int(season_id_raw))

    # PR-J13 — apply visibility filter in Python so all access
    # logic flows through user_can_view_race. Over-fetch +
    # post-filter; for the small ladder scale this is fine, and
    # the alternative (computing visibility in SQL) would push
    # auth into the query layer where it's easier to forget on
    # the next route. Pagination math runs against the filtered
    # set.
    viewer_id = current_user.id if current_user.is_authenticated else None
    all_rows = list(db.session.scalars(stmt))
    visible = [
        r for r in all_rows
        if official_service.user_can_view_race(r, user_id=viewer_id)
    ]
    total = len(visible)
    races = visible[(page - 1) * page_size : page * page_size]
    pages = max(1, (total + page_size - 1) // page_size)

    seasons = list(
        db.session.scalars(select(Season).order_by(Season.starts_at.desc()))
    )

    return render_template(
        "official/index.html",
        races=races,
        seasons=seasons,
        statuses=[s.value for s in OfficialRaceStatus],
        visibilities=[v.value for v in OfficialRaceVisibility],
        page=page,
        pages=pages,
        total=total,
        status=status,
        season_id=season_id_raw,
    )


@bp.route("/new", methods=["GET", "POST"])
@min_role_required(Role.ORGANIZER)
def new() -> object:
    form = CreateOfficialRaceForm()
    seasons = seasons_service.list_seasons()
    if form.validate_on_submit():
        # Browser submits naive local time via datetime-local; treat the
        # input as UTC so timestamps round-trip without timezone surprises.
        # (We could ask the browser for offset later; for now UTC is the
        # convention everywhere else in this codebase.)
        scheduled_at = form.scheduled_at.data
        if scheduled_at is not None and scheduled_at.tzinfo is None:
            from datetime import UTC

            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        try:
            race = official_service.create_race(
                official_service.CreateRaceRequest(
                    season_id=form.season_id.data,
                    name=form.name.data or "",
                    organizer_user_id=current_user.id,
                    preset_id=form.preset_id.data or None,
                    scheduled_at=scheduled_at,
                    max_players=form.max_players.data or None,
                    notes=form.notes.data or None,
                    race_season=form.race_season.data or None,
                    weather=form.weather.data or None,
                    ground_condition=form.ground_condition.data or None,
                    visibility=form.visibility.data or None,
                )
            )
        except official_service.OfficialError as exc:
            flash(str(exc))
        except track_conditions_service.TrackConditionError as exc:
            flash(str(exc))
        else:
            return redirect(url_for("official.detail", race_id=race.id))
    presets = [p for p in presets_service.list_presets() if p.enabled]
    # PR-L1 — surface the organizer's club for the visibility
    # selector explainer. None when their UserProfile.club_id is
    # unset (no friend code, never synced); the form still allows
    # picking Club but the service-layer guard rejects it with a
    # clean error pointing them at the profile refresh path.
    # PR-M1 — name comes from the first-class Club cache instead
    # of re-fetching the uma.moe trainer summary on every render.
    from ..services import clubs as clubs_service
    from ..services import profiles as profiles_service

    organizer_profile = profiles_service.get_or_create_profile(current_user)
    organizer_club_id = organizer_profile.club_id
    organizer_club = (
        clubs_service.get_club(organizer_club_id)
        if organizer_club_id is not None
        else None
    )
    organizer_club_name = organizer_club.name if organizer_club else None
    return render_template(
        "official/new.html",
        form=form,
        seasons=seasons,
        presets=presets,
        organizer_club_id=organizer_club_id,
        organizer_club_name=organizer_club_name,
    )


@bp.get("/<int:race_id>")
def detail(race_id: int) -> object:
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    # PR-J13 — Private races are 404 to non-invitees so the URL
    # doesn't even confirm the race exists. Organizer + invitees
    # + senior_organizer+ moderators see normally.
    viewer_id = current_user.id if current_user.is_authenticated else None
    if not official_service.user_can_view_race(race, user_id=viewer_id):
        abort(404)
    registrations = official_service.list_registrations(race_id)
    # PR-J13 — Private-race invitee list shown only when viewing
    # is gated (so we don't waste a query for Public races).
    invitees = (
        official_service.list_invitees(race_id)
        if race.visibility != "public"
        else []
    )
    # Pull existing race results so the page can render a per-result
    # "Add details" upload form (PR19b OCR enrichment). `.unique()`
    # is required because `OfficialRaceResult.skills` is
    # `lazy="joined"` on a collection — SQLAlchemy 2.x emits a JOIN
    # that produces duplicate parent rows and refuses to iterate
    # without explicit dedupe.
    from sqlalchemy import select as _select  # local import to keep top tidy

    results = list(
        db.session.scalars(
            _select(OfficialRaceResult)
            .where(OfficialRaceResult.official_race_id == race_id)
            .order_by(OfficialRaceResult.placement)
        ).unique()
    )
    room_code_form = RoomCodeForm()
    results_form = ResultsForm()
    csrf_form = CsrfOnlyForm()
    expired = official_service.is_room_code_expired(race)
    # PR-L1 — when the race is Club-only, surface the organizer's
    # club name on the chip so non-members understand what they're
    # looking at. PR-M1 — sourced from the first-class Club cache,
    # so we don't re-fetch the uma.moe trainer cache for the
    # organizer's friend_code on every detail render.
    club_id: int | None = None
    club_name: str | None = None
    allowed_clubs: list = []
    known_clubs: list = []
    if race.visibility == "club":
        from ..models import Club
        from ..services import clubs as clubs_service
        from ..services import profiles as profiles_service

        organizer_profile = profiles_service.get_or_create_profile(race.organizer)
        club_id = organizer_profile.club_id
        if club_id is not None:
            club = clubs_service.get_club(club_id)
            if club is not None:
                club_name = club.name
        # PR-O2 — additional clubs explicitly added by the organizer.
        allowed_clubs = official_service.list_allowed_clubs(race_id)
        # Quick-add picker: render every Club we already know about
        # as a chip the organizer can click to add. Filter out the
        # organizer's own club + already-allowed ones to avoid
        # offering no-op clicks. Limit to a sane chip count.
        from sqlalchemy import select as _sel

        already = {row.club_circle_id for row in allowed_clubs}
        if club_id is not None:
            already.add(club_id)
        stmt = _sel(Club).order_by(Club.name.asc().nulls_last()).limit(20)
        if already:
            stmt = stmt.where(~Club.circle_id.in_(already))
        known_clubs = list(db.session.scalars(stmt))
    return render_template(
        "official/detail.html",
        race=race,
        registrations=registrations,
        invitees=invitees,
        results=results,
        room_code_form=room_code_form,
        results_form=results_form,
        csrf_form=csrf_form,
        room_code_expired=expired,
        club_id=club_id,
        club_name=club_name,
        allowed_clubs=allowed_clubs,
        known_clubs=known_clubs,
    )


# ---------- PR-J13: invitee management ----------


@bp.post("/<int:race_id>/visibility")
@min_role_required(Role.ORGANIZER)
def change_visibility(race_id: int) -> object:
    """PR-J14 — toggle Public ↔ Private after creation. The
    Public→Private path auto-promotes existing registrants into
    the invitee allowlist so they keep access to a race they
    already joined."""
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    new_visibility = (request.form.get("visibility") or "").strip()
    try:
        official_service.change_visibility(
            race_id,
            new_visibility=new_visibility,
            by_user_id=current_user.id,
        )
    except official_service.RaceNotFoundError:
        abort(404)
    except PermissionDeniedError:
        abort(403)
    except official_service.InvalidRaceStateError as exc:
        flash(str(exc))
    except official_service.OfficialError as exc:
        flash(str(exc))
    else:
        flash(f"Race visibility set to {new_visibility}.")
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/invitees")
@min_role_required(Role.ORGANIZER)
def add_invitee(race_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    from ..services.permissions import assert_can_act_on_race

    try:
        assert_can_act_on_race(race, by_user_id=current_user.id)
    except PermissionDeniedError:
        abort(403)
    username = (request.form.get("invitee_username") or "").strip()
    if not username:
        flash("Enter a username to invite.")
        return redirect(url_for("official.detail", race_id=race_id))
    try:
        official_service.add_invitee(
            race_id,
            invitee_username=username,
            invited_by_user_id=current_user.id,
        )
        flash(f"Invited @{username}.")
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/invitees/<int:user_id>/remove")
@min_role_required(Role.ORGANIZER)
def remove_invitee(race_id: int, user_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    from ..services.permissions import assert_can_act_on_race

    try:
        assert_can_act_on_race(race, by_user_id=current_user.id)
    except PermissionDeniedError:
        abort(403)
    n = official_service.remove_invitee(race_id, user_id=user_id)
    flash(f"Removed {n} invitee(s).")
    return redirect(url_for("official.detail", race_id=race_id))


# ─── Multi-club allowlist (PR-O2) ────────────────────────────────


@bp.post("/<int:race_id>/clubs")
@min_role_required(Role.ORGANIZER)
def add_allowed_club(race_id: int) -> object:
    """Add a club to a Club-visibility race's allowlist. Same auth
    model as invitee management (organizer + senior_organizer+)."""
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    from ..services.permissions import assert_can_act_on_race

    try:
        assert_can_act_on_race(race, by_user_id=current_user.id)
    except PermissionDeniedError:
        abort(403)
    raw = (request.form.get("circle_id") or "").strip()
    if not raw or not raw.isdigit():
        flash("Enter a valid uma.moe club ID.")
        return redirect(url_for("official.detail", race_id=race_id))
    try:
        official_service.add_allowed_club(
            race_id,
            circle_id=int(raw),
            added_by_user_id=current_user.id,
        )
        flash(f"Allowed club #{raw}.")
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/clubs/<int:circle_id>/remove")
@min_role_required(Role.ORGANIZER)
def remove_allowed_club(race_id: int, circle_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    from ..services.permissions import assert_can_act_on_race

    try:
        assert_can_act_on_race(race, by_user_id=current_user.id)
    except PermissionDeniedError:
        abort(403)
    n = official_service.remove_allowed_club(race_id, circle_id=circle_id)
    flash(f"Removed {n} allowed club(s).")
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/open")
@min_role_required(Role.ORGANIZER)
def open_registration(race_id: int) -> object:
    try:
        official_service.open_registration(race_id, by_user_id=current_user.id)
    except official_service.RaceNotFoundError:
        abort(404)
    except PermissionDeniedError:
        abort(403)
    except official_service.InvalidRaceStateError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/close")
@min_role_required(Role.ORGANIZER)
def close_registration(race_id: int) -> object:
    try:
        official_service.close_registration(race_id, by_user_id=current_user.id)
    except official_service.RaceNotFoundError:
        abort(404)
    except PermissionDeniedError:
        abort(403)
    except official_service.InvalidRaceStateError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/register")
@login_required
def register(race_id: int) -> object:
    try:
        official_service.register(race_id, current_user.id)
    except official_service.RaceNotFoundError:
        abort(404)
    except official_service.AlreadyRegisteredError:
        flash("You are already registered.")
    except official_service.RaceFullError:
        flash("This race is full.")
    except official_service.InvalidRaceStateError as exc:
        flash(str(exc))
    except official_service.OfficialError as exc:
        # PR-J13 — covers "not invited to this private race". A
        # non-invitee shouldn't have reached this URL anyway since
        # the detail page 404s; keep the message generic so we
        # don't leak Private-race existence.
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/room-code")
@min_role_required(Role.ORGANIZER)
def room_code(race_id: int) -> object:
    form = RoomCodeForm()
    if form.validate_on_submit():
        try:
            official_service.set_room_code(
                race_id, form.room_code.data or "", by_user_id=current_user.id
            )
        except official_service.RaceNotFoundError:
            abort(404)
        except PermissionDeniedError:
            abort(403)
        except official_service.InvalidRaceStateError as exc:
            flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/results")
@min_role_required(Role.ORGANIZER)
def submit_results(race_id: int) -> object:
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)

    form = ResultsForm()
    if not form.validate_on_submit():
        return redirect(url_for("official.detail", race_id=race.id))

    lines: list[official_service.ResultLine] = []

    def _opt_int_field(name: str) -> int | None:
        raw = (request.form.get(name) or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    # Read pairs of placement_<reg_id> + uma_name_<reg_id> from form data.
    # PR-A5 — also read finish_time_<reg_id> / gate_<reg_id> /
    # fav_rank_<reg_id> when the OCR confirm flow populated them.
    # Manual entry leaves them blank (null), which is fine.
    for reg in official_service.list_registrations(race_id):
        placement_raw = request.form.get(f"placement_{reg.id}", "").strip()
        if not placement_raw:
            continue
        try:
            placement = int(placement_raw)
        except ValueError:
            flash(f"Invalid placement for {reg.user.username}.")
            return redirect(url_for("official.detail", race_id=race.id))
        lines.append(
            official_service.ResultLine(
                user_id=reg.user_id,
                placement=placement,
                uma_name=request.form.get(f"uma_name_{reg.id}", "").strip() or None,
                finish_time_or_lengths=(
                    request.form.get(f"finish_time_{reg.id}", "").strip() or None
                ),
                gate=_opt_int_field(f"gate_{reg.id}"),
                fav_rank=_opt_int_field(f"fav_rank_{reg.id}"),
            )
        )

    if not lines:
        flash("Enter at least one placement.")
        return redirect(url_for("official.detail", race_id=race.id))

    try:
        official_service.submit_results(
            race_id, lines, confirmed_by_user_id=current_user.id
        )
    except PermissionDeniedError:
        abort(403)
    except official_service.DuplicatePlacementError:
        flash("Two players cannot share the same placement.")
    return redirect(url_for("official.detail", race_id=race.id))


@bp.post("/<int:race_id>/results-screenshot")
@min_role_required(Role.ORGANIZER)
@limiter.limit("30 per hour")
def upload_result_screenshot(race_id: int) -> object:
    """Step 1 of the OCR-driven results flow: organiser uploads one or
    more result screenshots. We save each image, run the configured OCR
    provider, and redirect to a confirmation page where the parsed rows
    can be edited and assigned to specific registrations before
    submission.

    PR-OCR5 — multi-file parity with Draft. When the in-game result
    screen scrolls past one shot (large fields), the organiser can
    pick all screenshots in one go (or paste sequentially via the
    arm-button clipboard helper). Parsed rows are merged by placement
    across attempts — highest confidence wins on collisions. Rows
    without a placement (header / footer noise) are concatenated."""
    try:
        official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    form = ResultsScreenshotForm()
    if not form.validate_on_submit():
        for errs in form.errors.values():
            for err in errs:
                flash(err)
        return redirect(url_for("official.detail", race_id=race_id))

    files = [f for f in request.files.getlist("image") if f and f.filename]
    if not files:
        flash("Pick at least one screenshot.")
        return redirect(url_for("official.detail", race_id=race_id))

    attempts = []
    save_failures = 0
    for f in files:
        try:
            image = ocr_service.save_uploaded_image(
                f, uploader_user_id=current_user.id
            )
        except ocr_service.OcrError as exc:
            # PR-OCR8 — soft-fail per-file. A single bad upload no
            # longer cancels the whole batch.
            flash(f"{f.filename}: {exc}")
            save_failures += 1
            continue
        attempts.append(ocr_service.run_parse(image))

    if not attempts:
        return redirect(url_for("official.detail", race_id=race_id))

    # PR-OCR8 — visible parse count.
    parsed_ok = sum(
        1 for a in attempts if a.status == OcrParseStatus.PARSED
    )
    if len(files) > 1 or save_failures or parsed_ok != len(attempts):
        flash(
            f"Received {len(files)} file"
            + ("s" if len(files) != 1 else "")
            + f", parsed {parsed_ok}/{len(attempts)} attempt"
            + ("s" if len(attempts) != 1 else "")
            + (
                f" ({save_failures} rejected at upload)"
                if save_failures
                else ""
            )
            + "."
        )

    # Single screenshot: keep the original behaviour — the attempt's
    # parsed rows already drive the review page.
    if len(attempts) == 1:
        return redirect(
            url_for(
                "official.results_from_ocr",
                race_id=race_id,
                attempt_id=attempts[0].id,
            )
        )

    # Multi-screenshot: merge into the FIRST attempt's parsed_json so
    # the review URL is unchanged. Dedupe by placement (highest
    # confidence wins); placement-less rows are concatenated.
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
    # tracker sees a single replacement on a plain JSON column —
    # in-place mutations don't reliably persist without MutableDict.
    new_parsed = dict(primary.parsed_json or {})
    new_parsed["rows"] = merged_rows
    new_parsed["screenshot_image_ids"] = [
        a.uploaded_image_id for a in attempts
    ]
    primary.parsed_json = new_parsed
    db.session.commit()
    return redirect(
        url_for(
            "official.results_from_ocr",
            race_id=race_id,
            attempt_id=primary.id,
        )
    )


@bp.get("/<int:race_id>/results-from-ocr/<int:attempt_id>")
@min_role_required(Role.ORGANIZER)
def results_from_ocr(race_id: int, attempt_id: int) -> object:
    """Step 2: render the OCR-parsed rows alongside registrations so the
    organiser can assign each row to a player, edit the uma name, and
    submit through the standard /results endpoint."""
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    attempt = db.session.get(OcrParseAttempt, attempt_id)
    if attempt is None:
        abort(404)
    registrations = official_service.list_registrations(race_id)

    parsed_rows = (attempt.parsed_json or {}).get("rows", []) or []
    # Best-effort pre-match: case-insensitive substring match between the
    # OCR uma_name and registered usernames. Falls through cleanly when
    # there's no signal — organiser picks from the dropdown.
    suggestions = _match_ocr_to_registrations(parsed_rows, registrations)

    return render_template(
        "official/results_from_ocr.html",
        race=race,
        attempt=attempt,
        parsed_rows=parsed_rows,
        registrations=registrations,
        suggestions=suggestions,
        results_form=ResultsForm(),
    )


def _match_ocr_to_registrations(
    parsed_rows: list[dict], registrations
) -> dict[int, int]:
    """Return {parsed_row_index: registration_id} for confident matches."""
    by_username = {r.user.username.lower(): r.id for r in registrations}
    suggestions: dict[int, int] = {}
    used: set[int] = set()
    for i, row in enumerate(parsed_rows):
        name = (row.get("uma_name") or "").strip().lower()
        if not name:
            continue
        # exact match first
        if name in by_username and by_username[name] not in used:
            suggestions[i] = by_username[name]
            used.add(by_username[name])
            continue
        # substring fallback
        for uname, rid in by_username.items():
            if rid in used:
                continue
            if name in uname or uname in name:
                suggestions[i] = rid
                used.add(rid)
                break
    return suggestions


@bp.post("/<int:race_id>/results/<int:result_id>/details-screenshot")
@min_role_required(Role.ORGANIZER)
@limiter.limit("30 per hour")
def upload_result_details_screenshot(race_id: int, result_id: int) -> object:
    """Step 1 of the per-result OCR enrichment flow: upload one or more
    stat-screen screenshots for a specific completed result. Saves +
    parses each, then redirects to the confirmation step.

    PR-OCR5 — multi-file parity. An uma's profile typically spans two
    screens in-game; the organiser can pick both screenshots in one
    submit (or paste sequentially via the arm button). We pre-merge
    the per-attempt sheet extractions into the FIRST attempt's
    parsed_json so the confirm route sees the union without needing
    a new multi-attempt URL pattern."""
    try:
        official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    result = db.session.get(OfficialRaceResult, result_id)
    if result is None or result.official_race_id != race_id:
        abort(404)
    form = ResultsScreenshotForm()
    if not form.validate_on_submit():
        for errs in form.errors.values():
            for err in errs:
                flash(err)
        return redirect(url_for("official.detail", race_id=race_id))

    files = [f for f in request.files.getlist("image") if f and f.filename]
    if not files:
        flash("Pick at least one screenshot.")
        return redirect(url_for("official.detail", race_id=race_id))

    attempts = []
    save_failures = 0
    for f in files:
        try:
            image = ocr_service.save_uploaded_image(
                f, uploader_user_id=current_user.id
            )
        except ocr_service.OcrError as exc:
            # PR-OCR8 — soft-fail per-file instead of aborting the
            # whole batch. Previously a single bad upload (size /
            # mime issue) cancelled every prior parse. Now we
            # surface the per-file error + continue with the rest.
            flash(f"{f.filename}: {exc}")
            save_failures += 1
            continue
        attempts.append(ocr_service.run_parse(image))

    if not attempts:
        return redirect(url_for("official.detail", race_id=race_id))

    # PR-OCR8 — visible parse count so the user knows how many of
    # their uploads actually reached the server + parsed. Catches
    # JS-side regressions where only one file got submitted, and
    # OCR provider errors where a parse status went FAILED.
    parsed_ok = sum(
        1 for a in attempts if a.status == OcrParseStatus.PARSED
    )
    if len(files) > 1 or save_failures or parsed_ok != len(attempts):
        flash(
            f"Received {len(files)} file"
            + ("s" if len(files) != 1 else "")
            + f", parsed {parsed_ok}/{len(attempts)} attempt"
            + ("s" if len(attempts) != 1 else "")
            + (
                f" ({save_failures} rejected at upload)"
                if save_failures
                else ""
            )
            + "."
        )

    if len(attempts) == 1:
        return redirect(
            url_for(
                "official.result_details_from_ocr",
                race_id=race_id,
                result_id=result_id,
                attempt_id=attempts[0].id,
            )
        )

    # Multi-screenshot: extract each attempt's sheet independently,
    # merge via the sandbox's merge_extracts (union skills, first-
    # non-empty per stat/header/aptitude field), then pre-populate
    # the FIRST attempt's parsed_json with the merged stats + skill
    # names. The confirm route's existing extract-or-fallback logic
    # picks up the pre-merged values via the parsed_json["stats"] /
    # ["skills"] fallback path (re-running extract on the empty rows
    # of this combined view returns nothing, so the fallback wins).
    from ..services.ocr_uma_sheet import extract_uma_sheet, merge_extracts

    extracts = []
    for att in attempts:
        parsed = att.parsed_json or {}
        rows = parsed.get("rows") or []
        line_texts = [
            (r.get("raw_line") or r.get("uma_name") or "").strip()
            for r in rows
        ]
        line_texts = [t for t in line_texts if t]
        extracts.append(extract_uma_sheet(line_texts))
    merged = merge_extracts(extracts)

    primary = attempts[0]
    new_parsed = dict(primary.parsed_json or {})
    new_parsed["stats"] = merged.stats
    new_parsed["skills"] = [s["name_en"] for s in merged.skills]
    # PR-OCR9 — also persist aptitudes from the merge. Previously
    # the merge wrote stats + skills only; the confirm route would
    # then re-extract from cleared rows (returning empty
    # aptitudes) and fall back to result.aptitudes which is
    # similarly empty on first upload. Net effect: aptitudes from
    # screenshot 1 disappeared whenever a second screenshot was
    # also picked.
    new_parsed["aptitudes"] = merged.aptitudes
    # PR-A6 — same shape for uma_score: persist into parsed_json
    # so the confirm route's fallback chain finds it after the
    # rows get cleared.
    if merged.header.get("uma_score"):
        new_parsed["uma_score"] = merged.header["uma_score"]
    # Empty rows so the confirm route's re-extract pass returns empty
    # and the fallback to parsed_json's pre-merged stats/skills wins.
    new_parsed["rows"] = []
    new_parsed["screenshot_image_ids"] = [
        a.uploaded_image_id for a in attempts
    ]
    primary.parsed_json = new_parsed
    db.session.commit()

    return redirect(
        url_for(
            "official.result_details_from_ocr",
            race_id=race_id,
            result_id=result_id,
            attempt_id=primary.id,
        )
    )


@bp.get(
    "/<int:race_id>/results/<int:result_id>/details-from-ocr/<int:attempt_id>"
)
@min_role_required(Role.ORGANIZER)
def result_details_from_ocr(
    race_id: int, result_id: int, attempt_id: int
) -> object:
    """Step 2: confirmation page showing the screenshot + parsed stats +
    skill list. Organiser edits whatever's wrong, then submits to
    /details where the matching against UmaSkill happens."""
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    result = db.session.get(OfficialRaceResult, result_id)
    if result is None or result.official_race_id != race_id:
        abort(404)
    attempt = db.session.get(OcrParseAttempt, attempt_id)
    if attempt is None:
        abort(404)

    parsed = attempt.parsed_json or {}
    # PR-OCR4 — prefer the Uma-sheet-tuned extractor over the
    # race-result one. The race-result extractor's _extract_stats
    # used to put 1197 in every stat cell (it grabbed the first int
    # after each label); the sheet extractor matches positionally.
    # Skill candidates also come from the catalogue-matched list
    # (with inherited-variant dedupe) rather than per-row clusters
    # which split multi-skill rows badly.
    #
    # Fallback: if the sheet extractor produces nothing (e.g. the
    # organiser uploaded a non-sheet screenshot, OR the mock OCR
    # provider is in play for tests), drop back to the race-result
    # parse output that was already in parsed_json. This keeps the
    # confirm page useful for any image shape.
    from ..services.ocr_uma_sheet import extract_uma_sheet

    rows = parsed.get("rows") or []
    line_texts = [
        (r.get("raw_line") or r.get("uma_name") or "").strip()
        for r in rows
    ]
    line_texts = [t for t in line_texts if t]
    sheet = extract_uma_sheet(line_texts)
    sheet_skill_names = [s["name_en"] for s in sheet.skills]

    parsed_stats = sheet.stats or (parsed.get("stats") or {})
    parsed_skills = sheet_skill_names or (parsed.get("skills") or [])

    # PR-OCR6 — build a merged list of skill rows for the confirm
    # form. Each row is {"name_en", "candidate"}. `candidate` carries
    # the variant + inherited info that drives the template's
    # dropdown / badge render. Saved rows go first so they survive
    # a re-upload + re-confirm.
    #
    # Dedupe key is (name_lower, is_inherited) — NOT just name. The
    # in-game rule "slot 0 = innate, later slots = inherited" means
    # the same name_en can legitimately appear TWICE (innate +
    # inherited copy). Dedupe-by-name alone would collapse them.
    saved_rows: list[dict[str, object]] = []
    for assoc in result.skills:
        name = (
            assoc.skill.name_en if assoc.skill else (assoc.raw_ocr_text or "")
        )
        if not name:
            continue
        is_inh = bool(assoc.skill and assoc.skill.is_inherited)
        saved_rows.append(
            {
                "name_en": name,
                # Minimal candidate carrying just the badge signal —
                # no `variants` so the template renders a text input,
                # not a dropdown (the user already picked once).
                "candidate": {"is_inherited": is_inh},
            }
        )

    parsed_rows: list[dict[str, object]] = []
    if sheet.skills:
        # Have rich candidates — preserve variant lists.
        for c in sheet.skills:
            parsed_rows.append({"name_en": c["name_en"], "candidate": c})
    else:
        # Race-result extractor / mock fallback: bare strings only,
        # no variant info, no inherited flag.
        for name in parsed_skills:
            parsed_rows.append({"name_en": name, "candidate": None})

    def _dedupe_key(row: dict[str, object]) -> tuple[str, bool]:
        c = row.get("candidate") or {}
        return (
            ((row["name_en"] or "")).lower(),
            bool(c.get("is_inherited")),
        )

    seen_keys: set[tuple[str, bool]] = set()
    merged_skill_rows: list[dict[str, object]] = []
    for row in saved_rows + parsed_rows:
        key = _dedupe_key(row)
        if not key[0] or key in seen_keys:
            continue
        seen_keys.add(key)
        merged_skill_rows.append(row)

    # Pull every enabled skill name for the typeahead datalist. ~1.8k
    # rows fits in the rendered HTML (≈ 30KB) without JS — modern
    # browsers handle a datalist of this size fine. Sorted so the
    # browser's substring-match feels predictable.
    from sqlalchemy import select as _select

    from ..models import UmaSkill

    skill_names = list(
        db.session.scalars(
            _select(UmaSkill.name_en)
            .where(UmaSkill.enabled.is_(True))
            .order_by(UmaSkill.name_en.asc())
        )
    )
    # PR-A1 — aptitude pre-fill. PR-OCR9: three-tier fallback —
    # fresh extractor pass first (single-upload normal path);
    # parsed_json["aptitudes"] second (multi-upload merge pre-stored
    # them there); previously-saved result.aptitudes last (re-visiting
    # the form after an earlier save).
    parsed_aptitudes: dict = (
        sheet.aptitudes
        or (parsed.get("aptitudes") or {})
        or (result.aptitudes or {})
    )
    # PR-A6 — uma_score pre-fill. Same fallback shape as aptitudes:
    # fresh sheet extract → parsed_json (multi-upload merge) →
    # previously-saved value on the result row.
    parsed_uma_score: int | None = (
        sheet.header.get("uma_score")
        or parsed.get("uma_score")
        or result.uma_score
    )

    # PR-OCR10 — for multi-upload merges, parsed_json carries the
    # list of contributing image ids. The template renders all of
    # them in a strip so the organiser sees every screenshot they
    # actually uploaded, not just the primary one. Falls back to
    # just the primary attempt's image for single uploads.
    extra_image_ids: list[int] = list(
        parsed.get("screenshot_image_ids") or []
    )
    # Make sure the primary attempt's image is in the list and
    # comes first (it's the merge target).
    if attempt.image and attempt.image.id not in extra_image_ids:
        extra_image_ids.insert(0, attempt.image.id)
    elif attempt.image and extra_image_ids and extra_image_ids[0] != attempt.image.id:
        extra_image_ids = [attempt.image.id] + [
            i for i in extra_image_ids if i != attempt.image.id
        ]

    return render_template(
        "official/result_details_from_ocr.html",
        race=race,
        result=result,
        attempt=attempt,
        parsed_stats=parsed_stats,
        parsed_skills=parsed_skills,  # kept for back-compat
        merged_skill_rows=merged_skill_rows,
        parsed_aptitudes=parsed_aptitudes,
        parsed_uma_score=parsed_uma_score,
        skill_names=skill_names,
        screenshot_image_ids=extra_image_ids,
        csrf_form=CsrfOnlyForm(),
    )


@bp.post("/<int:race_id>/results/<int:result_id>/details")
@min_role_required(Role.ORGANIZER)
def submit_result_details(race_id: int, result_id: int) -> object:
    """Step 3: persist the confirmed stats + skills onto the result row.
    Skills are passed as one name per ``skill_name_<i>`` field; service
    matches them against UmaSkill.name_en case-insensitively and
    preserves raw text when no match is found."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    result = db.session.get(OfficialRaceResult, result_id)
    if result is None or result.official_race_id != race_id:
        abort(404)

    def _opt_int(name: str) -> int | None:
        v = (request.form.get(name) or "").strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    skill_names: list[str] = []
    i = 0
    while True:
        key = f"skill_name_{i}"
        if key not in request.form:
            break
        v = request.form.get(key, "").strip()
        if v:
            skill_names.append(v)
        i += 1

    # PR-A1 — aptitudes are submitted as `aptitude_<category>_<slot>`
    # fields (e.g. `aptitude_track_turf`, `aptitude_style_front`). We
    # collect them into the nested dict shape the model stores. Empty
    # values are skipped so the saved JSON only contains the slots
    # the organiser actually set; the template renders em-dashes for
    # any missing slots on the result detail page.
    _APT_SCHEMA = {
        "track": ("turf", "dirt"),
        "distance": ("sprint", "mile", "medium", "long"),
        "style": ("front", "pace", "late", "end"),
    }
    aptitudes: dict[str, dict[str, str]] = {}
    for cat, slots in _APT_SCHEMA.items():
        cat_out: dict[str, str] = {}
        for slot in slots:
            v = (
                request.form.get(f"aptitude_{cat}_{slot}") or ""
            ).strip().upper()
            if v:
                cat_out[slot] = v
        if cat_out:
            aptitudes[cat] = cat_out

    update = official_service.ResultDetailsUpdate(
        speed=_opt_int("speed"),
        stamina=_opt_int("stamina"),
        power=_opt_int("power"),
        guts=_opt_int("guts"),
        wisdom=_opt_int("wisdom"),
        strategy=(request.form.get("strategy") or "").strip() or None,
        skill_names=tuple(skill_names),
        aptitudes=aptitudes,
        uma_score=_opt_int("uma_score"),
    )
    try:
        official_service.submit_result_details(
            result_id, update, by_user_id=current_user.id
        )
        flash("Result details saved.")
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/cancel")
@min_role_required(Role.ORGANIZER)
def cancel(race_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        official_service.cancel_race(race_id, by_user_id=current_user.id)
        flash(f"Race #{race_id} cancelled.")
    except official_service.RaceNotFoundError:
        abort(404)
    except PermissionDeniedError:
        abort(403)
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/delete")
@min_role_required(Role.ADMIN)
def delete(race_id: int) -> object:
    """Hard-delete the race and everything FK'd to it. Admin-only —
    cancel is the right tool 99% of the time; this exists for
    smoke-test cleanup."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        official_service.delete_race(race_id, by_user_id=current_user.id)
        flash(f"Race #{race_id} permanently deleted.")
    except official_service.RaceNotFoundError:
        abort(404)
    return redirect(url_for("official.index"))


@bp.post("/<int:race_id>/registrations/<int:registration_id>/remove")
@login_required
def remove_registration(race_id: int, registration_id: int) -> object:
    """Two paths through this route:
       - Organizer+ removing any registration (existing UI).
       - A logged-in user unregistering themselves (PR follow-up to
         registration UX). Anything else is rejected here."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    reg = db.session.get(OfficialRaceRegistration, registration_id)
    if reg is None or reg.official_race_id != race_id:
        abort(404)
    is_organizer = current_user.has_at_least(Role.ORGANIZER)
    is_self = reg.user_id == current_user.id
    if not (is_organizer or is_self):
        abort(403)
    try:
        official_service.remove_registration(
            race_id, registration_id, by_user_id=current_user.id
        )
        flash("Registration removed." if is_organizer and not is_self else "You're no longer registered.")
    except official_service.RaceNotFoundError:
        abort(404)
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.get("/ladder/<int:season_id>")
def ladder(season_id: int) -> object:
    """Legacy redirect — the canonical surface is now /rankings
    (PR-N1). Kept so old bookmarks + Discord screenshots still
    land in the right spot."""
    return redirect(
        url_for("rankings.index", season=season_id, mode="official"),
        code=302,
    )
