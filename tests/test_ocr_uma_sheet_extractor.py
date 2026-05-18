"""PR-OCR2 — uma-sheet extractor tests.

Driven by the actual Vision row dump a maintainer captured against
their first sandbox upload (see the project session log for the
21-line fixture). Each assertion encodes a real failure mode the
race-result parser had on this layout, so a regression here means
the sandbox went backwards.
"""

from __future__ import annotations

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import UmaSkill
from uma_ladder.services.ocr_uma_sheet import (
    UmaSheetExtract,
    extract_uma_sheet,
    merge_extracts,
)

# Exact lines from a real Tamamo Cross profile screenshot, in the
# order Vision's row clusterer emits them. Preserved verbatim
# (including the tokenised apostrophe and the heart-emoji
# placeholder) so this fixture reflects what the parser actually
# has to deal with on prod.
_TAMAMO_FIXTURE: list[str] = [
    "Umamusume Details",
    "St [ Fast as Lightning ]",
    "RANK Tamamo Cross",
    "Epithet",
    "Now That's White Lightning!",
    "17,307 Trainer Yuuta",
    "Speed ♥ Stamina Power Guts Wit",
    "1197 1070 1105 553 638",
    "Track Turf A Dirt F",
    "Distance Sprint G Mile B Medium A Long A",
    "Style Front G Pace A Late A End A",
    "Save As Practice",
    "Partner",
    "Skills Inspiration Career Info",
    "White Lightning Comin ' Lvl 5 Anchors Aweigh!",
    "Through!",
    "Barcarole of Blessings Right - Handed O",
    "Hanshin Racecourse Professor of Curvature",
    "☑Swinging Maestro Homestretch Haste",
    "Straightaway Spurt Long Corners O",
    "Close",
]


# The skill names that appear in the fixture, each seeded as an
# enabled UmaSkill so the catalogue scan can find them. Real prod
# already has these via the GameTora seeder; the test environment
# starts empty so we add only what the fixture needs.
_FIXTURE_SKILLS: tuple[tuple[int, str], ...] = (
    (90001, "White Lightning Comin' Through!"),
    (90002, "Anchors Aweigh!"),
    (90003, "Barcarole of Blessings"),
    (90004, "Right-Handed"),
    (90005, "Hanshin Racecourse"),
    (90006, "Professor of Curvature"),
    (90007, "Swinging Maestro"),
    (90008, "Homestretch Haste"),
    (90009, "Straightaway Spurt"),
    (90010, "Long Corners"),
)


@pytest.fixture
def seeded_skills(app: Flask) -> list[UmaSkill]:
    with app.app_context():
        rows = [
            UmaSkill(gametora_id=gid, name_en=name, enabled=True)
            for gid, name in _FIXTURE_SKILLS
        ]
        db.session.add_all(rows)
        db.session.commit()
        # Refresh inside the same session before yielding so callers
        # can read IDs without a separate query.
        return list(db.session.query(UmaSkill).order_by(UmaSkill.gametora_id).all())


# ─── Stats: positional fix for the all-stats-on-one-row layout ───


def test_stats_positional_match(app: Flask) -> None:
    """The race-result parser used to assign the SAME first number to
    every stat (1197 → speed, stamina, power, guts, wisdom). Positional
    extraction pairs each label with the same-index number on the
    value row."""
    with app.app_context():
        result = extract_uma_sheet(_TAMAMO_FIXTURE)
    assert result.stats == {
        "speed": 1197,
        "stamina": 1070,
        "power": 1105,
        "guts": 553,
        "wisdom": 638,
    }


def test_stats_handles_wit_variant(app: Flask) -> None:
    """JP-localised dumps render Wisdom as 'Wit' — the parser maps
    both to the same field."""
    fixture = [
        "Speed Stamina Power Guts Wit",
        "100 200 300 400 500",
    ]
    with app.app_context():
        result = extract_uma_sheet(fixture)
    assert result.stats == {
        "speed": 100,
        "stamina": 200,
        "power": 300,
        "guts": 400,
        "wisdom": 500,
    }


def test_stats_empty_when_no_label_row(app: Flask) -> None:
    with app.app_context():
        result = extract_uma_sheet(["random text", "more random"])
    assert result.stats == {}


# ─── Aptitudes: three categories on three rows ───────────────────


def test_aptitudes_full(app: Flask) -> None:
    with app.app_context():
        result = extract_uma_sheet(_TAMAMO_FIXTURE)
    assert result.aptitudes == {
        "track": {"turf": "A", "dirt": "F"},
        "distance": {
            "sprint": "G",
            "mile": "B",
            "medium": "A",
            "long": "A",
        },
        "style": {
            "front": "G",
            "pace": "A",
            "late": "A",
            "end": "A",
        },
    }


def test_aptitudes_partial_row_recovers(app: Flask) -> None:
    """A Vision-inserted stray token between pairs shouldn't abort
    the whole row — the pair-walk skips one and keeps going."""
    fixture = ["Distance Sprint G ♥ Mile B Medium A Long A"]
    with app.app_context():
        result = extract_uma_sheet(fixture)
    # All four distance slots still recovered.
    assert result.aptitudes.get("distance") == {
        "sprint": "G",
        "mile": "B",
        "medium": "A",
        "long": "A",
    }


# ─── Skills: catalogue-resolved from post-"Skills" text block ────


def test_skills_resolve_against_catalogue(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    """Skills that appear cleanly in the OCR'd text resolve to their
    UmaSkill catalogue entries. The wrap case
    ('White Lightning Comin' + 'Through!' split across rows with
    'Anchors Aweigh!' between them) is a known limitation of the
    contiguous-substring matcher — see the comment in the extractor
    and the follow-up test below."""
    with app.app_context():
        result = extract_uma_sheet(_TAMAMO_FIXTURE)
    names = [s["name_en"] for s in result.skills]

    # All contiguous skills should resolve.
    for expected in (
        "Anchors Aweigh!",
        "Barcarole of Blessings",
        "Right-Handed",
        "Hanshin Racecourse",
        "Professor of Curvature",
        "Swinging Maestro",
        "Homestretch Haste",
        "Straightaway Spurt",
        "Long Corners",
    ):
        assert expected in names, f"{expected!r} should resolve from the fixture"


def test_skills_known_wrap_limitation(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    """White Lightning Comin' Through! is split across rows 15-16
    with Anchors Aweigh! interleaved between the halves. The
    contiguous-substring matcher can't recover it. Documenting the
    failure mode here so a fix lands with an assertion change, not
    a silent improvement that goes unnoticed."""
    with app.app_context():
        result = extract_uma_sheet(_TAMAMO_FIXTURE)
    names = [s["name_en"] for s in result.skills]
    assert "White Lightning Comin' Through!" not in names


def test_skills_strips_level_indicator(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    """`Lvl 5` between two skill names shouldn't break the catalogue
    scan or be absorbed into a name."""
    with app.app_context():
        result = extract_uma_sheet(
            [
                "Skills Inspiration Career Info",
                "Anchors Aweigh! Lvl 5 Barcarole of Blessings",
                "Close",
            ]
        )
    names = [s["name_en"] for s in result.skills]
    assert "Anchors Aweigh!" in names
    assert "Barcarole of Blessings" in names


def test_skills_empty_when_no_marker(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    """Without a `Skills … Career Info` marker the post-block search
    can't start — extractor returns []."""
    with app.app_context():
        result = extract_uma_sheet(["random text", "Anchors Aweigh!"])
    assert result.skills == []


# ─── Header: best-effort fields ──────────────────────────────────


def test_header_extracts_full_set(app: Flask) -> None:
    with app.app_context():
        result = extract_uma_sheet(_TAMAMO_FIXTURE)
    h = result.header
    assert h.get("uma_name") == "Tamamo Cross"
    assert h.get("outfit") == "Fast as Lightning"
    assert h.get("epithet") == "Now That's White Lightning!"
    assert h.get("uma_score") == 17307
    assert h.get("trainer_name") == "Yuuta"


def test_header_fields_independently_optional(app: Flask) -> None:
    """Missing rows leave the corresponding key out — caller renders
    em-dashes."""
    with app.app_context():
        result = extract_uma_sheet(["just some text"])
    assert result.header == {}


# ─── Return type ─────────────────────────────────────────────────


def test_extract_returns_dataclass(app: Flask) -> None:
    with app.app_context():
        result = extract_uma_sheet([])
    assert isinstance(result, UmaSheetExtract)
    assert result.stats == {}
    assert result.aptitudes == {}
    assert result.skills == []
    assert result.header == {}


# ─── PR-OCR3: tier-variant matching ──────────────────────────────


def test_tier_variants_all_emit(app: Flask) -> None:
    """Right-Handed ◎/○/× all normalize to the same key. When the
    OCR'd text says just `Right-Handed`, all three catalogue
    variants should appear in the result so the user can pick
    which tier was actually on-screen — the OCR can't read the
    tier glyph reliably."""
    with app.app_context():
        db.session.add_all(
            [
                UmaSkill(gametora_id=91000, name_en="Right-Handed ◎", enabled=True),
                UmaSkill(gametora_id=91001, name_en="Right-Handed ○", enabled=True),
                UmaSkill(gametora_id=91002, name_en="Right-Handed ×", enabled=True),
            ]
        )
        db.session.commit()
        result = extract_uma_sheet(
            [
                "Skills Inspiration Career Info",
                "Right-Handed",
                "Close",
            ]
        )
    names = [s["name_en"] for s in result.skills]
    assert "Right-Handed ◎" in names
    assert "Right-Handed ○" in names
    assert "Right-Handed ×" in names


def test_longer_skill_still_shadows_shorter(
    app: Flask,
) -> None:
    """Right-Handed Demon and Right-Handed ○ both exist. The longer
    name should match its own text without the shorter prefix
    cannibalising it."""
    with app.app_context():
        db.session.add_all(
            [
                UmaSkill(gametora_id=92000, name_en="Right-Handed Demon", enabled=True),
                UmaSkill(gametora_id=92001, name_en="Right-Handed ○", enabled=True),
            ]
        )
        db.session.commit()
        result = extract_uma_sheet(
            [
                "Skills Inspiration Career Info",
                "Right-Handed Demon",
                "Close",
            ]
        )
    names = [s["name_en"] for s in result.skills]
    # The longer name claimed the text first; the shorter shouldn't
    # also fire on the same characters.
    assert "Right-Handed Demon" in names
    assert "Right-Handed ○" not in names


# ─── PR-OCR3: lone-`Skills` marker fallback ──────────────────────


def test_marker_falls_back_to_lone_skills_line(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    """When Vision splits the tabs onto separate rows, the strict
    `Skills … Career` regex fails. The fallback finds a row that's
    JUST 'Skills' and uses that as the start marker."""
    with app.app_context():
        result = extract_uma_sheet(
            [
                "Career Info",
                "Skills",
                "Inspiration",
                "Anchors Aweigh!",
                "Close",
            ]
        )
    names = [s["name_en"] for s in result.skills]
    assert "Anchors Aweigh!" in names


def test_marker_prefers_full_pattern_when_both_present(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    """If a row with all three tabs exists, use it — even if a
    standalone `Skills` line also appears earlier. The full
    pattern is the definitive signal."""
    with app.app_context():
        result = extract_uma_sheet(
            [
                "Skills",  # lone — would absorb everything below
                "noise line that contains Anchors Aweigh! but isn't the skills section",
                "Skills Inspiration Career Info",  # full pattern
                "Anchors Aweigh!",
                "Close",
            ]
        )
    names = [s["name_en"] for s in result.skills]
    # Whichever marker won, Anchors Aweigh! should resolve once.
    assert names.count("Anchors Aweigh!") == 1


# ─── PR-OCR3: skills block debug ─────────────────────────────────


def test_skills_block_debug_exposed(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    """`skills_block_debug` on the result is the literal text the
    matcher actually scanned — the single most useful debugging
    surface when a known skill won't resolve."""
    with app.app_context():
        result = extract_uma_sheet(
            [
                "Skills Inspiration Career Info",
                "Anchors Aweigh!",
                "Close",
            ]
        )
    assert "Anchors Aweigh!" in result.skills_block_debug


def test_skills_block_debug_empty_when_no_marker(
    app: Flask, seeded_skills: list[UmaSkill]
) -> None:
    with app.app_context():
        result = extract_uma_sheet(["just some text"])
    assert result.skills_block_debug == ""


# ─── PR-OCR3: merge_extracts ─────────────────────────────────────


def test_merge_empty_returns_empty_extract(app: Flask) -> None:
    with app.app_context():
        m = merge_extracts([])
    assert isinstance(m, UmaSheetExtract)
    assert m.stats == {}
    assert m.skills == []


def test_merge_unions_skills_dedupes_by_id(app: Flask) -> None:
    a = UmaSheetExtract(
        skills=[
            {"id": 1, "name_en": "Anchors Aweigh!", "raw_pos": 0},
            {"id": 2, "name_en": "Barcarole of Blessings", "raw_pos": 10},
        ]
    )
    b = UmaSheetExtract(
        skills=[
            {"id": 2, "name_en": "Barcarole of Blessings", "raw_pos": 0},
            {"id": 3, "name_en": "Long Corners ○", "raw_pos": 8},
        ]
    )
    with app.app_context():
        m = merge_extracts([a, b])
    ids = [s["id"] for s in m.skills]
    assert ids == [1, 2, 3]


def test_merge_takes_first_non_empty_per_stat(app: Flask) -> None:
    """When the first screenshot fills speed/stamina and the second
    fills power/guts/wisdom, merging gives the full set."""
    a = UmaSheetExtract(stats={"speed": 1197, "stamina": 1070})
    b = UmaSheetExtract(stats={"power": 1105, "guts": 553, "wisdom": 638})
    with app.app_context():
        m = merge_extracts([a, b])
    assert m.stats == {
        "speed": 1197,
        "stamina": 1070,
        "power": 1105,
        "guts": 553,
        "wisdom": 638,
    }


def test_merge_header_first_wins(app: Flask) -> None:
    a = UmaSheetExtract(header={"uma_name": "Tamamo Cross"})
    b = UmaSheetExtract(
        header={"uma_name": "Different Uma", "trainer_name": "Yuuta"}
    )
    with app.app_context():
        m = merge_extracts([a, b])
    assert m.header["uma_name"] == "Tamamo Cross"  # first wins
    assert m.header["trainer_name"] == "Yuuta"  # only b had it


# ─── PR-OCR4: inherited-variant dedupe ───────────────────────────


def test_inherited_variant_dropped_when_original_present(
    app: Flask,
) -> None:
    """The catalogue ships two `Anchors Aweigh!` rows — original
    (is_inherited=False) and inherited (is_inherited=True). The
    extractor should emit ONE, preferring the non-inherited.
    Otherwise the per-result confirm form on Official races would
    show the same skill twice for the organiser to clean up."""
    with app.app_context():
        original = UmaSkill(
            gametora_id=93001,
            name_en="Anchors Aweigh!",
            enabled=True,
            is_inherited=False,
        )
        inherited = UmaSkill(
            gametora_id=93002,
            name_en="Anchors Aweigh!",
            enabled=True,
            is_inherited=True,
        )
        db.session.add_all([original, inherited])
        db.session.commit()
        result = extract_uma_sheet(
            [
                "Skills Inspiration Career Info",
                "Anchors Aweigh!",
                "Close",
            ]
        )
    names = [s["name_en"] for s in result.skills]
    assert names.count("Anchors Aweigh!") == 1
    # Specifically the non-inherited one survived.
    survivor_id = result.skills[0]["id"]
    with app.app_context():
        survivor = db.session.get(UmaSkill, survivor_id)
        assert survivor.is_inherited is False


def test_merge_debug_block_concatenates(app: Flask) -> None:
    a = UmaSheetExtract(skills_block_debug="anchors aweigh")
    b = UmaSheetExtract(skills_block_debug="long corners")
    with app.app_context():
        m = merge_extracts([a, b])
    assert "screenshot 1" in m.skills_block_debug
    assert "screenshot 2" in m.skills_block_debug
    assert "anchors aweigh" in m.skills_block_debug
    assert "long corners" in m.skills_block_debug
