"""Machine-facing API for the desktop race extractor.

Authenticated with a bearer token issued on the account page — NOT
with a session cookie, and not shared with any other site. Tokens
issued here are valid only for umaladder.moe; the sibling IT project
at training.umaladder.moe has its own separate tokens.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

from ..extensions import limiter
from ..services import api_tokens as tokens_service
from ..services import race_captures as captures_service

bp = Blueprint("api", __name__)


def token_required(view):
    """Authenticate `Authorization: Bearer <token>`.

    Deliberately returns the same generic message for missing and
    invalid tokens so the endpoint can't be used to probe which tokens
    exist.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        user = tokens_service.verify_token(token)
        if user is None:
            return jsonify({"error": "invalid or missing API token"}), 401
        g.api_user = user
        return view(*args, **kwargs)

    return wrapper


@bp.post("/race-captures")
@limiter.limit("30 per hour")
@token_required
def create_race_capture():
    """Accept one captured race.

    Idempotent on the room id: several participants in the same room
    may each upload it, and a re-upload is a 200 pointing at the
    existing capture rather than an error.
    """
    payload = request.get_json(silent=True)
    try:
        result = captures_service.ingest(
            payload, submitted_by_user_id=g.api_user.id
        )
    except captures_service.CaptureError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:  # noqa: BLE001 - never leak internals to a client
        current_app.logger.exception("race capture ingest failed")
        return jsonify({"error": "could not store capture"}), 500

    capture = result.capture
    body = {
        "id": capture.id,
        "status": capture.status,
        "created": result.created,
        "review_url": f"/captures/{capture.id}",
    }
    if not result.created:
        body["message"] = "this room was already uploaded"
    return jsonify(body), (201 if result.created else 200)


@bp.get("/race-captures/<int:capture_id>")
@token_required
def get_race_capture(capture_id: int):
    from ..extensions import db
    from ..models import RaceCapture

    capture = db.session.get(RaceCapture, capture_id)
    if capture is None or capture.submitted_by_user_id != g.api_user.id:
        # 404 rather than 403 — don't confirm that someone else's
        # capture exists.
        return jsonify({"error": "not found"}), 404
    return jsonify(captures_service.summarize(capture)), 200


@bp.get("/ping")
@token_required
def ping():
    """Lets the extractor verify its token + base URL before it bothers
    capturing anything — catching a token pasted into the wrong tool."""
    return jsonify({"ok": True, "user": g.api_user.username, "site": "umaladder.moe"})
