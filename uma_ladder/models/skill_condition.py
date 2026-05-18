"""PR-SK1 — skill catalog: condition predicates + stat buffs.

One row per UmaSkill (unique on skill_id). Predicates are nullable;
a NULL predicate means "skill doesn't care about this axis of the
race context". For a skill to "apply" to a race, every non-NULL
predicate must match the race context. Buff columns are signed
ints so × tier variants store as negative values (those skills
debuff the relevant stat when their condition is met).

Consumed by item 5 (gray out skills that don't apply) and item 6
(apply green-skill stat buffs to displayed stats). Both are still
queued; this model + table exists so the catalog can be populated
incrementally without code changes.

`is_dynamic = True` means: this skill has runtime-only conditions
we can't evaluate from static race context (e.g. "activates when
1.0L behind", "in the last 1/4 of the race"). Catalog still
recognizes the skill, but item 5 gray-outs it always (since we
can't know if it'll fire), and item 6 ignores it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SkillCondition(db.Model):
    __tablename__ = "skill_conditions"

    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("uma_skills.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ─── Race-context predicates ────────────────────────────────
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    surface: Mapped[str | None] = mapped_column(String(8), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(8), nullable=True)
    season: Mapped[str | None] = mapped_column(String(8), nullable=True)
    distance_category: Mapped[str | None] = mapped_column(
        String(8), nullable=True
    )
    strategy: Mapped[str | None] = mapped_column(String(8), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # PR-SK7 — "Firm" / "Good" / "Soft" / "Heavy". Matched against
    # `OfficialRace.ground_condition`. NULL = skill doesn't care.
    ground_condition: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # `True` = "applies at standard distances only" (1600/2000/2400/3200),
    # `False` = "applies at NON-standard distances only" (mirrors the
    # in-game "Non-Standard Distance" skill), `None` = doesn't care.
    is_standard_distance: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )

    # ─── Stat buffs when predicates match ───────────────────────
    # Signed: × tier variants store as negative (e.g. Right-Handed
    # × on a right-handed track debuffs Speed).
    buff_speed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    buff_stamina: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    buff_power: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    buff_guts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    buff_wisdom: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ─── Catalog metadata ───────────────────────────────────────
    is_dynamic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    notes: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=_utcnow, onupdate=_utcnow,
    )

    skill = relationship("UmaSkill", lazy="joined")

    def __repr__(self) -> str:
        return f"<SkillCondition skill_id={self.skill_id}>"
