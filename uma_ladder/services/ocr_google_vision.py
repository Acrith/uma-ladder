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
import re
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

# Uma Musume's result-summary screen renders placements as ordinals
# ("1st", "2nd", "3rd", "4th"...). Plain integers also need to be
# accepted for the legacy / official-race screenshots that already
# work in production.
_ORDINAL_RE = re.compile(r"^(\d+)(?:st|nd|rd|th)$", re.IGNORECASE)

# Known column-header / chrome strings that show up on the result
# screen as their own clusters. Filtering them out of the merge pass
# stops them from being absorbed into the previous entrant's
# uma_name (e.g. "Gold Ship 3:43.8 Yuuta RANK"). Lowercased for
# case-insensitive comparison.
_NOISE_TOKENS: frozenset[str] = frozenset({"rank"})

# Stat-screen detection: lowercased labels we recognise as Uma stats.
# The label appears once per row; the value is the closest plausible
# number on the same row (or directly below).
_STAT_LABELS: dict[str, str] = {
    "speed": "speed",
    "stamina": "stamina",
    "power": "power",
    "guts": "guts",
    "wisdom": "wisdom",
    "wit": "wisdom",   # JP localisation often renders Wisdom as "Wit"
}

# Stat values cap at 1200 in-game (raw) and ~2000 with bonuses; reject
# numbers wildly outside that range so a placement digit doesn't sneak in.
_STAT_VALUE_MIN = 1
_STAT_VALUE_MAX = 9999


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
    line_texts: list[str] = []
    for row in rows:
        row.sort(key=lambda w: w["x"])
        line = " ".join(w["text"] for w in row)
        line_texts.append(line)
        avg_conf = (
            sum(w.get("confidence", 0.0) for w in row) / len(row) if row else 0.0
        )
        first = row[0]["text"].strip().rstrip(".")
        placement: int | None = None
        if first.isdigit():
            n = int(first)
            if _PLACEMENT_MIN <= n <= _PLACEMENT_MAX:
                placement = n
        else:
            # Ordinal placements: "1st", "2nd", "3rd", "4th" etc. as
            # rendered on Uma Musume's result-summary screen.
            ordinal = _ORDINAL_RE.match(first)
            if ordinal:
                n = int(ordinal.group(1))
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

    parsed_rows = _merge_orphan_followups(parsed_rows)

    overall = (
        sum(r["confidence"] for r in parsed_rows) / len(parsed_rows)
        if parsed_rows
        else 0.0
    )

    stats = _extract_stats(line_texts)
    skill_candidates = _extract_skill_candidates(line_texts, stats)

    return OcrParse(
        raw_text=raw_text,
        rows=parsed_rows,
        stats=stats,
        skills=skill_candidates,
        confidence={"overall": round(overall, 3)},
    )


# Cap how many follow-up rows can be absorbed into one placement row.
# Game UI typically has 1-3 lines of metadata per player (uma name,
# epithet, stats blob); higher caps risk eating an unrelated footer.
_MAX_FOLLOWUP_ABSORPTIONS = 3


def _merge_orphan_followups(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse non-placement rows into the preceding placement row.

    Vision's clusterer puts each visual line into its own Y-cluster.
    On the Uma Musume result-summary screen each player produces 2-4
    visual lines (uma portrait tag, uma name, epithet, sometimes a
    stats / time line) which arrive here as 2-4 separate rows — one
    with the placement digit, the rest with no placement and just
    text. This pass walks rows in OCR order and absorbs the text of
    the trailing non-placement rows into the placement row's
    `uma_name` so consumers see one row per actual race entrant.

    Stops absorbing on:
      - the next row with a placement (next entrant)
      - end of list
      - cap reached (`_MAX_FOLLOWUP_ABSORPTIONS`)

    The dropped non-placement rows are *not* returned — they're
    redundant with the merged uma_name. The route layer can still
    surface them by re-parsing raw_text if forensic review is needed.
    """
    if not rows:
        return rows
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(rows):
        cur = rows[i]
        if cur.get("placement") is None:
            # Pre-placement noise — keep it visible so a UI that
            # filters to placement-only rows doesn't silently lose
            # the data, but it doesn't get merged into anything.
            out.append(cur)
            i += 1
            continue
        absorbed = 0
        merged = dict(cur)
        j = i + 1
        while (
            j < len(rows)
            and rows[j].get("placement") is None
            and absorbed < _MAX_FOLLOWUP_ABSORPTIONS
        ):
            extra = (rows[j].get("raw_line") or "").strip()
            # Skip column-header / chrome rows so a "RANK" cluster
            # sitting between two entrants doesn't get glued to the
            # previous entrant's uma_name. The cap still increments
            # so a stretch of pure-noise rows eventually breaks out.
            if extra and extra.lower() not in _NOISE_TOKENS:
                if merged.get("uma_name"):
                    merged["uma_name"] = f"{merged['uma_name']} {extra}".strip()
                else:
                    merged["uma_name"] = extra
                merged["raw_line"] = (
                    f"{merged.get('raw_line', '')} {rows[j].get('raw_line', '')}"
                ).strip()
            absorbed += 1
            j += 1
        out.append(merged)
        i = j
    return out


_STAT_VALUE_RE = re.compile(r"\b(\d{1,4})\b")
_STAT_LABEL_RE = re.compile(
    r"\b(" + "|".join(re.escape(lbl) for lbl in _STAT_LABELS) + r")\b",
    re.IGNORECASE,
)


def _extract_stats(line_texts: list[str]) -> dict[str, int]:
    """Pair each Speed/Stamina/Power/Guts/Wisdom label with the nearest
    plausible number on the same line. Falls back to scanning the next
    line when the label row has no number (some game UIs put the label
    above the value).
    """
    stats: dict[str, int] = {}
    for i, line in enumerate(line_texts):
        for match in _STAT_LABEL_RE.finditer(line):
            key = _STAT_LABELS[match.group(1).lower()]
            if key in stats:
                continue
            tail = line[match.end():]
            value = _first_int_in_range(tail)
            if value is None and i + 1 < len(line_texts):
                value = _first_int_in_range(line_texts[i + 1])
            if value is not None:
                stats[key] = value
    return stats


def _first_int_in_range(s: str) -> int | None:
    for raw in _STAT_VALUE_RE.findall(s):
        n = int(raw)
        if _STAT_VALUE_MIN <= n <= _STAT_VALUE_MAX:
            return n
    return None


def _extract_skill_candidates(
    line_texts: list[str], stats: dict[str, int]
) -> list[str]:
    """Return clustered lines that look like skill names — i.e. drop
    pure-number rows, stat-label rows, and lines that begin with a
    placement digit. The fuzzy matcher in services.official is the
    authority on what's a real skill; the organiser confirms before save.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in line_texts:
        text = line.strip()
        if not text:
            continue
        # Drop placement rows ("1 Special Week", "2 Silence Suzuka").
        first = text.split(maxsplit=1)[0].rstrip(".")
        if first.isdigit():
            n = int(first)
            if _PLACEMENT_MIN <= n <= _PLACEMENT_MAX:
                continue
        # Drop pure-number rows.
        if text.replace(",", "").replace(".", "").isdigit():
            continue
        # Drop stat-label rows when we already extracted a value for that
        # label — keeps the candidate list short and the organiser's
        # editor focused on real skill candidates.
        if stats and _STAT_LABEL_RE.search(text):
            continue
        # Skill names are typically short. Anything > 80 chars is almost
        # certainly a misclustered paragraph; skip rather than confuse
        # the matcher with sentence-length raw OCR text.
        if len(text) > 80:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
