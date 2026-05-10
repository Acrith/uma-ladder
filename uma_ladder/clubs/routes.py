from __future__ import annotations

from flask import Blueprint, abort, render_template

from ..services import clubs as clubs_service

bp = Blueprint("clubs", __name__, template_folder="templates")


@bp.get("/<int:circle_id>")
def detail(circle_id: int) -> object:
    """Public Club page (PR-M1).

    Shows the cached uma.moe club name + the roster of Uma Ladder
    members of that club. Public — same visibility model as
    `/profiles/<username>`. The roster is built purely from
    `UserProfile.club_id` matches; we don't enumerate every uma.moe
    member of the club (we don't fetch that data today).

    A 404 is returned only when the Club row is unknown AND the
    roster is empty. If a stray UserProfile.club_id mirror exists
    for a circle we haven't ingested a Club row for yet (e.g. user's
    profile sync wrote first, the upsert path hasn't fired), the
    page still renders with the roster — the name header just
    falls back to "Club #<id>".
    """
    club = clubs_service.get_club(circle_id)
    members = clubs_service.list_members(circle_id)
    if club is None and not members:
        abort(404)
    return render_template(
        "clubs/detail.html",
        circle_id=circle_id,
        club=club,
        members=members,
    )
