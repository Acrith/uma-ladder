from flask import Blueprint

bp = Blueprint("draft", __name__)


@bp.get("/")
def index() -> str:
    return "draft placeholder"
