from flask import Blueprint

bp = Blueprint("notifications", __name__)


@bp.get("/")
def index() -> str:
    return "notifications placeholder"
