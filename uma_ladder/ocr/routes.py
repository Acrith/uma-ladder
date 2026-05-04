from flask import Blueprint

bp = Blueprint("ocr", __name__)


@bp.get("/")
def index() -> str:
    return "ocr placeholder"
