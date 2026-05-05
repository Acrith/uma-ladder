from __future__ import annotations

from flask import Blueprint, render_template, request
from sqlalchemy import func, select

from ..extensions import db
from ..models import Role, UmaSkill
from ..services.permissions import min_role_required

bp = Blueprint("skills", __name__, template_folder="templates")

PAGE_SIZE = 60


@bp.get("/")
@min_role_required(Role.ORGANIZER)
def index() -> object:
    page = max(1, request.args.get("page", 1, type=int))
    q = (request.args.get("q") or "").strip()
    filter_kind = (request.args.get("kind") or "").strip()  # "unique" | "inherited" | ""

    stmt = (
        select(UmaSkill)
        .where(UmaSkill.enabled.is_(True))
        .order_by(UmaSkill.name_en.asc(), UmaSkill.gametora_id.asc())
    )
    if q:
        stmt = stmt.where(func.lower(UmaSkill.name_en).contains(q.lower()))
    if filter_kind == "unique":
        stmt = stmt.where(UmaSkill.is_unique.is_(True))
    elif filter_kind == "inherited":
        stmt = stmt.where(UmaSkill.is_inherited.is_(True))

    total = db.session.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    skills = list(
        db.session.scalars(
            stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
        )
    )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return render_template(
        "skills/index.html",
        skills=skills,
        page=page,
        pages=pages,
        total=total,
        q=q,
        filter_kind=filter_kind,
    )
