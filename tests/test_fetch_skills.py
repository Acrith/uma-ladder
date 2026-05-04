"""Skill fetcher tests — exercise manifest → payload → coerced rows."""

from __future__ import annotations

import json

from uma_ladder.services.fetch_gametora import (
    FakeGameToraTransport,
    HttpResponse,
    _coerce_skill,
    fetch_skills,
)


def _ok(payload: dict | list) -> HttpResponse:
    return HttpResponse(
        ok=True,
        body=json.dumps(payload).encode("utf-8"),
        status_code=200,
        error=None,
    )


def _row(**overrides) -> dict:
    base = {
        "id": 110031,
        "enname": "It's Going to Be Me",
        "jpname": "絶対は、ボクだ",
        "endesc": "If you're chasing after another girl in the front…",
        "jpdesc": "最終コーナー以降に前の方で追いすがっていると…",
        "iconid": 20013,
        "char": [100302],  # owned by character 100302 → unique
        "rarity": 4,
    }
    base.update(overrides)
    return base


def _transport_with(version: str, skills_payload: list[dict]) -> FakeGameToraTransport:
    return FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok(
                {"skills": version}
            ),
            f"https://gametora.com/data/umamusume/skills.{version}.json": _ok(
                skills_payload
            ),
        }
    )


# ---------- _coerce_skill ----------


def test_coerce_happy_path_marks_unique_when_char_set() -> None:
    s = _coerce_skill(_row())
    assert s is not None
    assert s.gametora_id == 110031
    assert s.name_en == "It's Going to Be Me"
    assert s.name_jp == "絶対は、ボクだ"
    assert s.is_unique is True
    assert s.is_inherited is False
    assert s.parent_gametora_id is None
    assert s.icon_id == 20013
    assert s.rarity == 4


def test_coerce_no_char_means_not_unique() -> None:
    """Generic skills (without `char`) shouldn't be flagged unique."""
    s = _coerce_skill(_row(char=[]))
    assert s is not None
    assert s.is_unique is False


def test_coerce_missing_id_returns_none() -> None:
    assert _coerce_skill(_row(id=None)) is None


def test_coerce_missing_name_returns_none() -> None:
    assert _coerce_skill(_row(enname="", jpname="x")) is None


def test_coerce_inherited_with_parent() -> None:
    s = _coerce_skill(
        _row(id=910031),
        parent_id=110031,
        is_inherited=True,
    )
    assert s is not None
    assert s.is_inherited is True
    assert s.parent_gametora_id == 110031
    # Inherited rows are never marked unique even if their `char` array
    # is set — the unique tag belongs to the canonical skill.
    assert s.is_unique is False


def test_coerce_builds_image_url_from_icon_id() -> None:
    s = _coerce_skill(_row(iconid=20013))
    assert s is not None
    assert s.image_url == (
        "https://gametora.com/images/umamusume/skill_icons/utx_ico_skill_20013.png"
    )


def test_coerce_no_image_url_when_icon_id_missing() -> None:
    s = _coerce_skill(_row(iconid=None))
    assert s is not None
    assert s.image_url is None


def test_coerce_prefers_endesc_over_machine_translated_desc_en() -> None:
    s = _coerce_skill(
        _row(endesc="curated EN copy", desc_en="machine translated")
    )
    assert s is not None
    assert s.description_en == "curated EN copy"


# ---------- fetch_skills ----------


def test_fetch_emits_main_and_gene_version_rows() -> None:
    transport = _transport_with(
        "v1",
        [
            {
                **_row(id=110031, enname="Main"),
                "gene_version": _row(id=910031, enname="Inherited"),
            }
        ],
    )
    skills = fetch_skills(transport, delay_seconds=0)
    assert len(skills) == 2
    # Output is sorted by gametora_id ascending.
    assert skills[0].gametora_id == 110031
    assert skills[0].is_inherited is False
    assert skills[1].gametora_id == 910031
    assert skills[1].is_inherited is True
    assert skills[1].parent_gametora_id == 110031


def test_fetch_skips_rows_without_id_or_name() -> None:
    transport = _transport_with(
        "v1",
        [
            _row(id=110031),
            _row(id=None),
            _row(id=110099, enname=""),
        ],
    )
    skills = fetch_skills(transport, delay_seconds=0)
    assert [s.gametora_id for s in skills] == [110031]


def test_fetch_dedupes_repeat_ids() -> None:
    """If the upstream payload accidentally lists the same skill twice
    (e.g. through multi-region duplication) we keep just one."""
    transport = _transport_with(
        "v1",
        [
            _row(id=110031, enname="First"),
            _row(id=110031, enname="Second"),
        ],
    )
    skills = fetch_skills(transport, delay_seconds=0)
    assert len(skills) == 1
    assert skills[0].name_en == "First"
