from __future__ import annotations

from pathlib import Path

import click
from flask import Flask
from flask.cli import AppGroup

from .services.fetch_gametora import (
    fetch_characters,
    fetch_g1_races,
    fetch_outfits,
    fetch_skills,
    write_characters_snapshot,
    write_g1_races_snapshot,
    write_outfits_snapshot,
    write_skills_snapshot,
)
from .services.seed_characters import DEFAULT_SEED_PATH as DEFAULT_CHARACTER_SEED_PATH
from .services.seed_characters import seed_characters
from .services.seed_g1 import import_g1_races
from .services.seed_outfits import DEFAULT_SEED_PATH as DEFAULT_OUTFIT_SEED_PATH
from .services.seed_outfits import seed_outfits
from .services.seed_presets import seed_custom_presets
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


def register_cli(app: Flask) -> None:
    app.cli.add_command(uma_cli)
