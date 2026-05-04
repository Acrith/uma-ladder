from flask import Blueprint

bp = Blueprint("presets", __name__)


@bp.get("/")
def index() -> str:
    return "presets placeholder"
