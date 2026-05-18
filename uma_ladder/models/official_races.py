from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .enums import OfficialRaceStatus, OfficialRaceVisibility, RegistrationStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OfficialRace(db.Model):
    __tablename__ = "official_races"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    preset_id: Mapped[int | None] = mapped_column(
        ForeignKey("race_presets.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OfficialRaceStatus.DRAFT, index=True
    )
    organizer_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_opens_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    room_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    room_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    max_players: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PR-J13 — race targeting. PUBLIC (default) → visible on the
    # index. PRIVATE → only organizer + invitees see it. CLUB
    # reserved for the deferred follow-up.
    visibility: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=OfficialRaceVisibility.PUBLIC,
        server_default=OfficialRaceVisibility.PUBLIC.value,
        index=True,
    )
    # Race-day conditions (PR-G3). Nullable so legacy rows still load;
    # the organizer form encourages — but doesn't yet require — values.
    race_season: Mapped[str | None] = mapped_column(String(8), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ground_condition: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # PR-SK9 — actual count of uma at the start (players + CPU).
    # Drives the gate-bracket calculation that gates Inner / Outer
    # Post Proficiency. Null until the organizer enters it via
    # the results submission form, in which case post-number
    # skills stay bright (we can't compute brackets without it).
    participant_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    preset = relationship("RacePreset", lazy="joined")
    season = relationship("Season", lazy="joined")
    organizer = relationship("User", lazy="joined", foreign_keys=[organizer_user_id])

    def __repr__(self) -> str:
        return f"<OfficialRace {self.id} {self.name!r} status={self.status}>"


class OfficialRaceRegistration(db.Model):
    __tablename__ = "official_race_registrations"
    __table_args__ = (
        UniqueConstraint(
            "official_race_id", "user_id", name="uq_official_race_registrations_race_user"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    official_race_id: Mapped[int] = mapped_column(
        ForeignKey("official_races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RegistrationStatus.REGISTERED
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user = relationship("User", lazy="joined")


class OfficialRaceResult(db.Model):
    __tablename__ = "official_race_results"
    __table_args__ = (
        UniqueConstraint(
            "official_race_id", "user_id", name="uq_official_race_results_race_user"
        ),
        UniqueConstraint(
            "official_race_id", "placement", name="uq_official_race_results_race_placement"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    official_race_id: Mapped[int] = mapped_column(
        ForeignKey("official_races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uma_character_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_characters.id", ondelete="SET NULL"), nullable=True
    )
    uma_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    placement: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    speed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stamina: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wisdom: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PR-A1 — Track / Distance / Style aptitude grades from the
    # uma profile sheet, parsed by services.ocr_uma_sheet and
    # persisted via the per-result confirm form. Shape matches the
    # extractor output 1:1::
    #   {"track":    {"turf": "A", "dirt": "F"},
    #    "distance": {"sprint": "G", "mile": "B",
    #                 "medium": "A", "long": "A"},
    #    "style":    {"front": "G", "pace": "A",
    #                 "late": "A", "end": "A"}}
    # Each grade is one of G F E D C B A S. Nullable so historical
    # rows (without OCR enrichment) survive the migration.
    aptitudes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # PR-A5 — row-parser fields the OCR pipeline has been pulling
    # off the result screen all along. `finish_time_or_lengths` is
    # the winner's race time ("3:43.8") OR the gap to the winner
    # for non-winners ("1/2 L" / "Nose" / "Distance"). `gate` is the
    # starting gate number; `fav_rank` is the pre-race favorite
    # number. All nullable: historical rows + manual entries don't
    # have to fill these.
    finish_time_or_lengths: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    gate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fav_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PR-A6 — the in-game "uma score" integer (e.g. 17,307). Pulled
    # from the sheet OCR header parse, persisted via the per-result
    # confirm form. Rendered next to the uma name on the race detail
    # card as the overall rank badge (G..SS+..Ug⁶ via
    # services/stat_ranks.rank_points_index).
    uma_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user = relationship("User", lazy="joined", foreign_keys=[user_id])
    uma_character = relationship("UmaCharacter", lazy="joined")
    skills = relationship(
        "OfficialRaceResultSkill",
        cascade="all, delete-orphan",
        order_by="OfficialRaceResultSkill.position",
        lazy="joined",
    )


class OfficialRaceResultSkill(db.Model):
    """Skills observed on a player's Uma in a specific official race
    result. ``skill_id`` is null when OCR returned a name we couldn't
    match against the UmaSkill catalogue — ``raw_ocr_text`` is preserved
    so an organiser can fix the catalogue or correct the spelling later."""

    __tablename__ = "official_race_result_skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    official_race_result_id: Mapped[int] = mapped_column(
        ForeignKey("official_race_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_skills.id", ondelete="SET NULL"), nullable=True
    )
    raw_ocr_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    skill = relationship("UmaSkill", lazy="joined")


class OfficialRaceInvitee(db.Model):
    """PR-J13 — invitee list for Private races.

    A row here means the organizer explicitly invited this user
    to a Private race. The presence of a row gates both viewing
    and registration; without one (and not being the organizer
    or a moderator+) the race is invisible.

    Plain INSERT/DELETE managed from the race detail page; no
    accept/decline state — being invited IS the access grant. If
    the future Club-only path needs more nuance (e.g. revoked
    invites), this table can grow a status column later.
    """

    __tablename__ = "official_race_invitees"
    __table_args__ = (
        UniqueConstraint(
            "official_race_id",
            "user_id",
            name="uq_official_race_invitees_race_user",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    official_race_id: Mapped[int] = mapped_column(
        ForeignKey("official_races.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invited_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user = relationship(
        "User", lazy="joined", foreign_keys=[user_id]
    )
    invited_by = relationship(
        "User", lazy="joined", foreign_keys=[invited_by_user_id]
    )


class OfficialRaceClubAllowlist(db.Model):
    """PR-O2 — additional clubs allowed to view a Club-visibility race.

    The organizer's own club is implicitly always allowed (handled in
    the visibility check, NOT a row here). This table only tracks
    *additional* clubs the organizer has added — allied-club
    tournaments, cross-club friendlies, etc.

    Visibility check for a CLUB race becomes:
      - organizer + senior_organizer+ override (existing)
      - viewer's UserProfile.club_id == organizer's UserProfile.club_id (PR-L1)
      - viewer's UserProfile.club_id IN allowlist (PR-O2)
      - viewer in invitee list (PR-J13 override)

    `club_circle_id` FKs the first-class `clubs` table from PR-M1
    so deleting a Club row would cascade rows here away — but the
    Club table is a metadata cache, never user-deleted, so the
    cascade is defense-in-depth. `added_by_user_id` is nullable +
    SET NULL so deleting the user (who shouldn't exist; this is
    a hobbyist project, but safety first) doesn't cascade-delete
    the allowlist row and leave the race silently visible to the
    wrong audience.
    """

    __tablename__ = "official_race_club_allowlist"

    official_race_id: Mapped[int] = mapped_column(
        ForeignKey("official_races.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    club_circle_id: Mapped[int] = mapped_column(
        ForeignKey("clubs.circle_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    added_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    club = relationship("Club", lazy="joined")
    added_by = relationship(
        "User", lazy="joined", foreign_keys=[added_by_user_id]
    )
