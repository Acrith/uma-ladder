"""Google Cloud Vision OCR provider.

Uses the Vision REST API's DOCUMENT_TEXT_DETECTION feature with API-key
auth. We pick API-key over service-account JSON to keep deploy simple
(one env var, no file mounts). Restrict the key to the Vision API + the
server's IP in the GCP console.

The provider parses the response into row-shaped dicts by:
- collecting every word with its bounding-box centre Y and left X,
- clustering words into rows whose Y centres lie within half the median
  word height,
- sorting each row by X and joining the tokens.

The first token of a row is treated as a placement number when it parses
as an integer in [1, 30]. The remaining tokens become `uma_name`.

The transport layer is injectable so tests use FakeVisionTransport and
never hit the network. UrllibVisionTransport is used in production via
stdlib only — no `requests` dep.
"""

from __future__ import annotations

import base64
import json
import statistics
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import current_app

from .ocr import OcrParse, OcrProvider

VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"
_PLACEMENT_MIN = 1
_PLACEMENT_MAX = 30  # generous upper bound for any future race format


# ---------- Transport ----------


@dataclass(frozen=True)
class VisionResponse:
    ok: bool
    body: dict[str, Any] | None
    status_code: int | None
    error: str | None


class VisionTransport(ABC):
    @abstractmethod
    def post(self, url: str, body: dict[str, Any], *, timeout: float = 20.0) -> VisionResponse:
        ...


class UrllibVisionTransport(VisionTransport):
    def post(
        self, url: str, body: dict[str, Any], *, timeout: float = 20.0
    ) -> VisionResponse:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return VisionResponse(
                    ok=200 <= resp.status < 300,
                    body=payload,
                    status_code=resp.status,
                    error=None,
                )
        except urllib.error.HTTPError as exc:
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, OSError):
                err_body = None
            return VisionResponse(
                ok=False, body=err_body, status_code=exc.code, error=str(exc)
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return VisionResponse(ok=False, body=None, status_code=None, error=str(exc))


@dataclass
class FakeVisionTransport(VisionTransport):
    """Test transport that records calls and returns a canned response."""

    response: VisionResponse

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(
        self, url: str, body: dict[str, Any], *, timeout: float = 20.0  # noqa: ARG002
    ) -> VisionResponse:
        self.calls.append((url, body))
        return self.response


# ---------- Provider ----------


class GoogleVisionOcrProvider(OcrProvider):
    name = "google_vision"

    def __init__(self, api_key: str, *, transport: VisionTransport | None = None) -> None:
        if not api_key:
            raise RuntimeError("GoogleVisionOcrProvider requires an api_key")
        self.api_key = api_key
        self.transport = transport or UrllibVisionTransport()

    def parse(self, image_path: Path) -> OcrParse:
        with open(image_path, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        body = {
            "requests": [
                {
                    "image": {"content": content},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                }
            ]
        }
        url = f"{VISION_API_URL}?key={self.api_key}"
        result = self.transport.post(url, body)
        if not result.ok:
            detail = result.error or f"HTTP {result.status_code}"
            raise RuntimeError(f"Vision API call failed: {detail}")

        responses = (result.body or {}).get("responses") or []
        if not responses:
            return OcrParse(rows=[], confidence={"note": "empty response"})
        first = responses[0]
        if "error" in first:
            msg = first["error"].get("message", "unknown")
            raise RuntimeError(f"Vision API error: {msg}")
        annotation = first.get("fullTextAnnotation") or {}
        return _parse_annotation(annotation)


def build_google_vision_provider() -> GoogleVisionOcrProvider:
    """Factory used by services/ocr.get_provider() for the lazy import path."""
    api_key = current_app.config.get("GOOGLE_VISION_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_VISION_API_KEY is required when OCR_PROVIDER=google_vision"
        )
    transport = current_app.extensions.get("uma_ladder.vision_transport")
    return GoogleVisionOcrProvider(api_key=api_key, transport=transport)


def set_vision_transport(transport: VisionTransport | None) -> None:
    """Override the transport (used by tests)."""
    if transport is None:
        current_app.extensions.pop("uma_ladder.vision_transport", None)
    else:
        current_app.extensions["uma_ladder.vision_transport"] = transport


# ---------- Response parsing ----------


def _word_text(word: dict[str, Any]) -> str:
    return "".join(s.get("text", "") for s in word.get("symbols", []))


def _word_height(vertices: list[dict[str, int]]) -> float:
    ys = [v.get("y", 0) for v in vertices]
    return float(max(ys) - min(ys)) if ys else 0.0


def _word_centre(vertices: list[dict[str, int]]) -> tuple[float, float]:
    if not vertices:
        return (0.0, 0.0)
    xs = [v.get("x", 0) for v in vertices]
    ys = [v.get("y", 0) for v in vertices]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _parse_annotation(annotation: dict[str, Any]) -> OcrParse:
    raw_text = annotation.get("text") or None
    pages = annotation.get("pages") or []
    words: list[dict[str, Any]] = []
    for page in pages:
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                for word in para.get("words", []):
                    text = _word_text(word).strip()
                    if not text:
                        continue
                    vertices = (word.get("boundingBox") or {}).get("vertices") or []
                    if not vertices:
                        continue
                    cx, cy = _word_centre(vertices)
                    words.append(
                        {
                            "text": text,
                            "x": cx,
                            "y": cy,
                            "height": _word_height(vertices),
                            "confidence": float(word.get("confidence", 0.0)),
                        }
                    )

    if not words:
        return OcrParse(raw_text=raw_text, rows=[], confidence={"note": "no words"})

    heights = [w["height"] for w in words if w["height"] > 0]
    median_height = statistics.median(heights) if heights else 12.0
    threshold = max(4.0, median_height / 2.0)

    words.sort(key=lambda w: (w["y"], w["x"]))
    rows: list[list[dict[str, Any]]] = [[words[0]]]
    for w in words[1:]:
        cur = rows[-1]
        cur_y = sum(c["y"] for c in cur) / len(cur)
        if abs(w["y"] - cur_y) <= threshold:
            cur.append(w)
        else:
            rows.append([w])

    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        row.sort(key=lambda w: w["x"])
        line = " ".join(w["text"] for w in row)
        avg_conf = (
            sum(w.get("confidence", 0.0) for w in row) / len(row) if row else 0.0
        )
        first = row[0]["text"].strip().rstrip(".")
        placement: int | None = None
        if first.isdigit():
            n = int(first)
            if _PLACEMENT_MIN <= n <= _PLACEMENT_MAX:
                placement = n
        if placement is not None:
            uma_name = " ".join(w["text"] for w in row[1:]).strip() or None
        else:
            uma_name = line or None
        parsed_rows.append(
            {
                "placement": placement,
                "uma_name": uma_name,
                "raw_line": line,
                "confidence": round(avg_conf, 3),
            }
        )

    overall = (
        sum(r["confidence"] for r in parsed_rows) / len(parsed_rows)
        if parsed_rows
        else 0.0
    )
    return OcrParse(
        raw_text=raw_text,
        rows=parsed_rows,
        confidence={"overall": round(overall, 3)},
    )
