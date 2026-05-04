from __future__ import annotations

from pathlib import Path

import click
from flask import Flask
from flask.cli import AppGroup

from .services.seed_characters import seed_characters
from .services.seed_g1 import import_g1_races
from .services.seed_presets import seed_custom_presets

uma_cli = AppGroup("uma", help="Uma Ladder maintenance commands.")


@uma_cli.command("seed-characters")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override the default seed JSON path.",
)
def cmd_seed_characters(file_path: Path | None) -> None:
    """Idempotent upsert of curated Uma characters."""
    report = seed_characters(file_path)
    click.echo(
        f"seed-characters: inserted={report.inserted} "
        f"updated={report.updated} skipped={report.skipped} total={report.total}"
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


def register_cli(app: Flask) -> None:
    app.cli.add_command(uma_cli)
