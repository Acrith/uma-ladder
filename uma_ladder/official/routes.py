from flask import Blueprint

bp = Blueprint("official", __name__)


@bp.get("/")
def index() -> str:
    return "official placeholder"
