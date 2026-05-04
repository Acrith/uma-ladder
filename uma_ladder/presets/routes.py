from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, url_for

from ..extensions import db
from ..models import RacePreset, Role
from ..services import presets as presets_service
from ..services.permissions import min_role_required

bp = Blueprint("presets", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    presets = presets_service.list_presets()
    return render_template("presets/index.html", presets=presets)


@bp.post("/<int:preset_id>/toggle")
@min_role_required(Role.ADMIN)
def toggle(preset_id: int) -> object:
    preset = db.session.get(RacePreset, preset_id)
    if preset is None:
        abort(404)
    presets_service.set_enabled(preset_id, not preset.enabled)
    return redirect(url_for("presets.index"))
