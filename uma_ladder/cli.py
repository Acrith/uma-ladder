from __future__ import annotations

from pathlib import Path

import click
from flask import Flask
from flask.cli import AppGroup

from .services.fetch_gametora import (
    fetch_characters,
    fetch_g1_races,
    fetch_outfits,
    fetch_skill_conditions,
    fetch_skills,
    write_characters_snapshot,
    write_g1_races_snapshot,
    write_outfits_snapshot,
    write_skill_conditions_snapshot,
    write_skills_snapshot,
)
from .services.seed_characters import DEFAULT_SEED_PATH as DEFAULT_CHARACTER_SEED_PATH
from .services.seed_characters import seed_characters
from .services.seed_g1 import import_g1_races
from .services.seed_outfits import DEFAULT_SEED_PATH as DEFAULT_OUTFIT_SEED_PATH
from .services.seed_outfits import seed_outfits
from .services.seed_presets import seed_custom_presets
from .services.seed_skill_conditions import (
    DEFAULT_SEED_PATH as DEFAULT_SKILL_CONDITIONS_SEED_PATH,
)
from .services.seed_skill_conditions import seed_skill_conditions
from .services.seed_skills import DEFAULT_SEED_PATH as DEFAULT_SKILL_SEED_PATH
from .services.seed_skills import seed_skills

uma_cli = AppGroup("uma", help="Uma Ladder maintenance commands.")


@uma_cli.command("seed-characters")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the default seed JSON path.",
)
@click.option(
    "--prune-missing",
    is_flag=True,
    default=False,
    help=(
        "Disable any DB character whose slug is not in the snapshot. "
        "Use after switching to a smaller region-filtered snapshot."
    ),
)
def cmd_seed_characters(file_path: Path | None, prune_missing: bool) -> None:
    """Idempotent upsert of curated Uma characters."""
    report = seed_characters(file_path, prune_missing=prune_missing)
    click.echo(
        f"seed-characters: inserted={report.inserted} "
        f"updated={report.updated} skipped={report.skipped} "
        f"pruned={report.pruned} total={report.total}"
    )


@uma_cli.command("seed-presets")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the default custom_races.txt path.",
)
def cmd_seed_presets(file_path: Path | None) -> None:
    """Idempotent upsert of custom race presets parsed from §22 appendix."""
    report = seed_custom_presets(file_path)
    click.echo(
        f"seed-presets: inserted={report.inserted} "
        f"updated={report.updated} skipped={report.skipped} total={report.total}"
    )


@uma_cli.command("import-g1-races")
@click.option(
    "--source-file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the default data/seeds/g1_races.json path.",
)
def cmd_import_g1_races(file_path: Path | None) -> None:
    """Idempotent upsert of G1 race presets from a checked-in GameTora snapshot."""
    report = import_g1_races(file_path)
    click.echo(
        f"import-g1-races: inserted={report.inserted} "
        f"updated={report.updated} skipped={report.skipped} total={report.total}"
    )


@uma_cli.command("fetch-gametora-characters")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Where to write the snapshot JSON. Defaults to the file consumed "
        "by `seed-characters` so a refresh is fetch → commit → seed."
    ),
)
@click.option(
    "--region",
    type=click.Choice(["global", "en", "ko", "zh_tw", "tw", "jp", "all"]),
    default="global",
    show_default=True,
    help=(
        "Filter to characters playable on this server. 'global' is the EN "
        "server (default). 'jp' is the Japan server (more characters). "
        "'all' skips the region filter entirely."
    ),
)
@click.option(
    "--include-non-playable",
    is_flag=True,
    default=False,
    help="Include event/NPC characters regardless of the region filter.",
)
def cmd_fetch_gametora_characters(
    out_path: Path | None, region: str, include_non_playable: bool
) -> None:
    """One-off: fetch the latest character snapshot from GameTora.

    Polite single-request fetch via the public manifest. Writes a JSON
    file in the shape `seed-characters` consumes. Run this manually
    when GameTora updates and you want to refresh local data; do not
    invoke from a request handler.
    """
    target = out_path or DEFAULT_CHARACTER_SEED_PATH
    click.echo(f"fetch-gametora-characters: region={region} → {target}")
    rows = fetch_characters(
        region=region, include_non_playable=include_non_playable
    )
    write_characters_snapshot(rows, target, region=region)
    click.echo(
        f"fetch-gametora-characters: wrote {len(rows)} characters "
        f"(region={region}, include_non_playable={include_non_playable})"
    )


@uma_cli.command("fetch-gametora-outfits")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output JSON path. Defaults to data/seeds/uma_outfits.json.",
)
@click.option(
    "--region",
    type=click.Choice(["global", "en", "ko", "zh_tw", "tw", "jp", "all"]),
    default="global",
    show_default=True,
    help="Filter to outfits released on this server.",
)
@click.option(
    "--include-unreleased",
    is_flag=True,
    default=False,
    help="Include outfits not yet released on the chosen region.",
)
def cmd_fetch_gametora_outfits(
    out_path: Path | None, region: str, include_unreleased: bool
) -> None:
    """One-off: fetch outfit snapshot from GameTora's character-cards data."""
    target = out_path or DEFAULT_OUTFIT_SEED_PATH
    click.echo(f"fetch-gametora-outfits: region={region} → {target}")
    rows = fetch_outfits(
        region=region, only_released=not include_unreleased
    )
    write_outfits_snapshot(rows, target, region=region)
    click.echo(
        f"fetch-gametora-outfits: wrote {len(rows)} outfits "
        f"(region={region}, only_released={not include_unreleased})"
    )


@uma_cli.command("seed-outfits")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the default seed JSON path.",
)
@click.option(
    "--prune-missing",
    is_flag=True,
    default=False,
    help="Disable any outfit not in the snapshot.",
)
def cmd_seed_outfits(file_path: Path | None, prune_missing: bool) -> None:
    """Idempotent upsert of UmaOutfit rows by (character, costume_id)."""
    report = seed_outfits(file_path, prune_missing=prune_missing)
    click.echo(
        f"seed-outfits: inserted={report.inserted} "
        f"updated={report.updated} skipped={report.skipped} "
        f"pruned={report.pruned} orphaned={report.orphaned} "
        f"total={report.total}"
    )


@uma_cli.command("fetch-gametora-g1-races")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output JSON path. Defaults to data/seeds/g1_races.json.",
)
def cmd_fetch_gametora_g1_races(out_path: Path | None) -> None:
    """One-off: fetch the full G1 race list from GameTora.

    Writes a snapshot in the same shape `import-g1-races` consumes,
    filtered to G1 grade and JRA / Oi venues (foreign races skipped).
    """
    from .services.seed_g1 import DEFAULT_SEED_PATH as DEFAULT_G1_SEED_PATH

    target = out_path or DEFAULT_G1_SEED_PATH
    click.echo(f"fetch-gametora-g1-races: writing to {target}")
    rows = fetch_g1_races()
    write_g1_races_snapshot(rows, target)
    click.echo(f"fetch-gametora-g1-races: wrote {len(rows)} G1 races")


@uma_cli.command("fetch-gametora-skills")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output JSON path. Defaults to data/seeds/uma_skills.json.",
)
def cmd_fetch_gametora_skills(out_path: Path | None) -> None:
    """One-off: fetch the skills snapshot from GameTora.

    Polite single-request fetch via the public manifest. Writes a JSON
    file in the shape `seed-skills` consumes. Includes inherited
    (gene_version) variants as separate rows.
    """
    target = out_path or DEFAULT_SKILL_SEED_PATH
    click.echo(f"fetch-gametora-skills: writing to {target}")
    rows = fetch_skills()
    write_skills_snapshot(rows, target)
    click.echo(f"fetch-gametora-skills: wrote {len(rows)} skills")


@uma_cli.command("seed-skills")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the default seed JSON path.",
)
@click.option(
    "--prune-missing",
    is_flag=True,
    default=False,
    help="Disable any skill not in the snapshot.",
)
def cmd_seed_skills(file_path: Path | None, prune_missing: bool) -> None:
    """Idempotent upsert of UmaSkill rows by gametora_id."""
    report = seed_skills(file_path, prune_missing=prune_missing)
    click.echo(
        f"seed-skills: inserted={report.inserted} "
        f"updated={report.updated} skipped={report.skipped} "
        f"pruned={report.pruned} total={report.total}"
    )


# PR-SK2 — populate the skill_conditions catalog from GameTora.
# Two-step refresh: fetch JSON to disk, commit, run seed. Same
# pattern as fetch-gametora-skills + seed-skills.


@uma_cli.command("fetch-gametora-skill-conditions")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output JSON path. Defaults to data/seeds/skill_conditions.json.",
)
def cmd_fetch_gametora_skill_conditions(out_path: Path | None) -> None:
    """One-off: fetch the skill-condition catalog from GameTora.

    Reads the same upstream skills payload `fetch-gametora-skills`
    uses; projects each row's ``condition_groups`` to our static-
    only schema. Skills with dynamic (runtime-only) conditions are
    written with `is_dynamic=true` so item 5 still recognises them.
    """
    target = out_path or DEFAULT_SKILL_CONDITIONS_SEED_PATH
    click.echo(f"fetch-gametora-skill-conditions: writing to {target}")
    rows = fetch_skill_conditions()
    write_skill_conditions_snapshot(rows, target)
    dynamic = sum(1 for r in rows if r.is_dynamic)
    click.echo(
        f"fetch-gametora-skill-conditions: wrote {len(rows)} entries "
        f"({dynamic} dynamic, {len(rows) - dynamic} static)"
    )


@uma_cli.command("seed-skill-conditions")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the default seed JSON path.",
)
def cmd_seed_skill_conditions(file_path: Path | None) -> None:
    """Idempotent upsert of SkillCondition rows.

    Skips entries whose `gametora_id` doesn't match an existing
    `UmaSkill` — run `seed-skills` first when adding entries for
    newly-released skills.
    """
    report = seed_skill_conditions(file_path)
    click.echo(
        f"seed-skill-conditions: inserted={report.inserted} "
        f"updated={report.updated} "
        f"skipped_no_skill={report.skipped_no_skill} "
        f"skipped_invalid={report.skipped_invalid} "
        f"total={report.total}"
    )


@uma_cli.command("ocr-test")
@click.argument(
    "image_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--provider",
    type=str,
    default=None,
    help=(
        "Override OCR_PROVIDER for this single run. Useful for comparing "
        "manual / mock / google_vision against the same image."
    ),
)
def cmd_ocr_test(image_path: Path, provider: str | None) -> None:
    """Smoke-test the configured OCR provider against a local image.

    Prints structured output (rows / stats / skills / confidence) so
    you can verify Google Vision is producing sensible results before
    real races flow through the UI. Does NOT write to the database —
    no UploadedImage row, no OcrParseAttempt row, no commits.

    Example:
        flask uma ocr-test ~/screenshots/result.png
        flask uma ocr-test ~/screenshots/stats.png --provider google_vision
    """
    from flask import current_app

    from .services import ocr as ocr_service

    if provider is not None:
        current_app.config["OCR_PROVIDER"] = provider

    chosen = ocr_service.get_provider()
    click.echo(f"ocr-test: provider={chosen.name} image={image_path}")

    try:
        parse = chosen.parse(image_path)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"ocr-test: provider raised: {exc}", err=True)
        raise SystemExit(1) from exc

    # Don't dump huge raw_text — show first 200 chars.
    raw = parse.raw_text or ""
    raw_preview = raw if len(raw) <= 200 else raw[:200] + "…"

    click.echo(f"  raw_text:   {raw_preview!r}")
    click.echo(f"  rows:       {len(parse.rows)}")
    for i, row in enumerate(parse.rows[:10]):
        click.echo(f"    [{i}] {row}")
    if len(parse.rows) > 10:
        click.echo(f"    ... ({len(parse.rows) - 10} more rows)")
    click.echo(f"  stats:      {parse.stats}")
    click.echo(f"  skills:     {len(parse.skills)} candidate(s)")
    for s in parse.skills[:15]:
        click.echo(f"    · {s}")
    if len(parse.skills) > 15:
        click.echo(f"    ... ({len(parse.skills) - 15} more)")
    click.echo(f"  confidence: {parse.confidence}")


@uma_cli.command("ocr-backfill-draft-links")
@click.option(
    "--window-seconds",
    type=int,
    default=600,
    help="Max seconds between attempt.confirmed_at and match.completed_at.",
)
def cmd_ocr_backfill_draft_links(window_seconds: int) -> None:
    """Re-stamp draft_match_id on confirmed OCR attempts (PR-J4).

    Idempotent — only touches rows where draft_match_id IS NULL.
    Same heuristic the c5ce51843265 migration applies, exposed as a
    CLI for re-runs after data imports or if a future bug skips
    stamping a confirmation.
    """
    from .services import ocr as ocr_service

    linked = ocr_service.backfill_draft_match_links(
        window_seconds=window_seconds
    )
    click.echo(f"ocr-backfill-draft-links: linked {linked} attempt(s)")


def register_cli(app: Flask) -> None:
    app.cli.add_command(uma_cli)


@uma_cli.command("issue-api-token")
@click.argument("username")
@click.option("--name", default="race extractor", help="Label for this machine.")
def cmd_issue_api_token(username: str, name: str) -> None:
    """Mint a bearer token for the desktop race extractor.

    The plaintext is printed once and cannot be recovered afterwards —
    only its SHA-256 digest is stored. Paste it into the extractor's
    uma-race-config.json.
    """
    from .services import api_tokens as tokens_service
    from .services.auth import find_user_by_username

    user = find_user_by_username(username)
    if user is None:
        raise click.ClickException(f"no such user: {username}")
    _row, plaintext = tokens_service.issue_token(user, name=name)
    click.echo(f"token for {user.username} ({name}):")
    click.echo(f"  {plaintext}")
    click.echo("")
    click.echo("Shown once only. This token is for umaladder.moe —")
    click.echo("it is NOT valid on training.umaladder.moe.")


@uma_cli.command("reapply-capture")
@click.argument("capture_id", type=int)
@click.argument("actor_username")
def cmd_reapply_capture(capture_id: int, actor_username: str) -> None:
    """Re-push a confirmed capture's stats, aptitudes and skills.

    Confirming is one-shot, so a race saved while the enrichment path
    had a gap stays incomplete forever otherwise. This re-reads the
    stored payload and writes the details again; placements and points
    are untouched.
    """
    from .services import race_captures as captures_service
    from .services.auth import find_user_by_username

    actor = find_user_by_username(actor_username)
    if actor is None:
        raise click.ClickException(f"no such user: {actor_username}")
    try:
        n = captures_service.reapply_details(capture_id, actor_user_id=actor.id)
    except captures_service.CaptureError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"re-applied details to {n} result(s) from capture {capture_id}")
