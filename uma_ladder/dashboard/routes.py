from flask import Blueprint

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index() -> str:
    return "dashboard placeholder"
