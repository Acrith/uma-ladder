from __future__ import annotations

from pathlib import Path

import click
from flask import Flask
from flask.cli import AppGroup

from .services.seed_characters import seed_characters

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


def register_cli(app: Flask) -> None:
    app.cli.add_command(uma_cli)
