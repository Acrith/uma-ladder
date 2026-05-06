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

# Skill-rank prefix on an "epithet" line. Each Uma Musume entrant
# block starts with one of these visually above the placement row:
#   "SS Unpredictable End"
#   "S Now That's White Lightning ! End"
#   "A+ <text>"
# Used to detect rows that introduce the *next* entrant so the
# forward merge stops before stealing them. Order matters: longer
# prefixes ("SS", "S+", "A+", ...) tested before single-letter ones.
_EPITHET_RE = re.compile(
    r"^(SS|S\+|S|A\+|A|B\+|B|C\+|C|D\+|D|F|G)\s+\S",
    re.IGNORECASE,
)


def _looks_like_epithet(text: str) -> bool:
    return bool(_EPITHET_RE.match(text.strip()))


def _parse_placement_row_fields(text: str) -> dict[str, Any]:
    """Decompose a merged placement row into structured fields.

    Input is the post-merge `uma_name` (or `raw_line`) of a placement
    row, e.g.
      "SS Unpredictable End 8 Gold Ship 3:43.8 Yuuta No. 1 Fav"

    Output keys (any of which may be absent if extraction fails — the
    review form treats them all as optional and falls back to the
    raw merged text as-is):

      epithet_rank   "SS"
      epithet        "Unpredictable End"
      gate           8
      uma_name       "Gold Ship"          ← the clean field used by the form
      time_or_lengths "3:43.8" or "3 1/2 L" or "Nose"
      player_name    "Yuuta"
      fav_rank       1

    Best-effort regex extraction, peeled from the outside in. Each
    successfully matched chunk is stripped before the next regex
    runs, so order matters: player → epithet → time/lengths → gate.
    """
    out: dict[str, Any] = {}
    if not text:
        return out
    work = text.strip()

    # Player + fav, e.g. "Yuuta No. 1 Fav" — anchored at the end.
    # `\w+` (instead of `\S+`) excludes time / decimal artifacts like
    # "3:43.8" that the greedier match used to absorb into the
    # player_name capture group.
    player_match = re.search(
        r"(\w+)\s+No\.\s*(\d+)\s+Fav\s*$",
        work,
        re.IGNORECASE,
    )
    if player_match:
        out["player_name"] = player_match.group(1).strip()
        out["fav_rank"] = int(player_match.group(2))
        work = work[: player_match.start()].strip()

    # Epithet at the start: skill rank prefix + descriptive text up to
    # the next "<digit> " boundary (which is the gate column).
    epithet_match = re.match(
        r"^(SS|S\+|S|A\+|A|B\+|B|C\+|C|D\+|D|F|G)\s+(.+?)(?=\s+\d|$)",
        work,
        re.IGNORECASE,
    )
    if epithet_match:
        out["epithet_rank"] = epithet_match.group(1).upper()
        out["epithet"] = epithet_match.group(2).strip()
        work = work[epithet_match.end():].strip()

    # Finishing time (1st-place row): "M:SS.S" or "MM:SS.S".
    time_match = re.search(r"\b(\d+:\d+\.\d+)\s*$", work)
    if time_match:
        out["time_or_lengths"] = time_match.group(1)
        work = work[: time_match.start()].strip()
    else:
        # Lengths-back gap: "3 1/2 L", "1/2 L", "Nose", "Head", "Neck"
        # (the latter three may or may not carry an "L" suffix).
        length_match = re.search(
            r"(\d+(?:\s+\d+/\d+)?\s*L|\d+/\d+\s*L|(?:Nose|Head|Neck)(?:\s*L)?)\s*$",
            work,
            re.IGNORECASE,
        )
        if length_match:
            out["time_or_lengths"] = length_match.group(1).strip()
            work = work[: length_match.start()].strip()

    # Leading gate digit, then the rest is the uma name.
    gate_match = re.match(r"^(\d+)\s+(.+)$", work)
    if gate_match:
        out["gate"] = int(gate_match.group(1))
        out["uma_name"] = gate_match.group(2).strip() or None
    elif work:
        out["uma_name"] = work
    return out


def _tighten_punctuation(text: str) -> str:
    """Glue Vision-tokenised punctuation back to the preceding word.

    Vision OCR tokenises punctuation as standalone "words", so when
    we join cluster words with spaces we end up with artifacts:

      "Now That's White Lightning ! End" → "Now That's White Lightning! End"
      "3 : 43.8"                         → "3:43.8"
      "No . 1 Fav"                       → "No. 1 Fav"

    Only tightens patterns that are unambiguous:
      - Terminal punctuation (.,;:!?) glued to preceding word.
        Note ":" is excluded here since "Subject: foo" reads naturally
        with a trailing space.
      - ":" between digits (race times like "3 : 43.8").
      - "." between digits (decimal artifacts; defensive).
    """
    if not text:
        return text
    text = re.sub(r"\s+([!?,;.])", r"\1", text)
    text = re.sub(r"(\d)\s*:\s*(\d)", r"\1:\2", text)
    text = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", text)
    return text

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
        line = _tighten_punctuation(" ".join(w["text"] for w in row))
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
            uma_name = _tighten_punctuation(
                " ".join(w["text"] for w in row[1:]).strip()
            ) or None
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

    # PR-I3 — peel structured fields out of each merged placement row.
    # Stored alongside the original `uma_name` (now a "raw_uma_name"
    # backup) so the review form has both clean inputs and the full
    # text fallback.
    for row in parsed_rows:
        if row.get("placement") is None:
            continue
        raw_uma_name = row.get("uma_name") or ""
        fields = _parse_placement_row_fields(raw_uma_name)
        row["raw_uma_name"] = raw_uma_name
        if "uma_name" in fields:
            row["uma_name"] = fields["uma_name"]
        for key in ("gate", "time_or_lengths", "player_name", "fav_rank",
                    "epithet_rank", "epithet"):
            if key in fields:
                row[key] = fields[key]

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
    """Collapse multi-line entrant blocks into one placement row each.

    The Uma Musume result-summary screen renders each entrant across
    THREE visual lines, in order top-to-bottom:

      [skill-rank + epithet + position-tag]   "SS Unpredictable End"
      [ordinal + gate + uma + time/lengths]   "1st 8 Gold Ship 3:43.8"
      [player-name + fav-rank]                "Yuuta No. 1 Fav"

    Vision's Y-clusterer emits each as its own row. A naive forward-
    only merge ("absorb the next N orphans into this placement row")
    *steals* the next entrant's epithet line because it sits between
    placement N's player line and placement N+1's main line.

    Two-pass merge:
      1. Route every epithet-like orphan FORWARD to the next
         placement row in the OCR sequence — that's where it
         visually belongs.
      2. For each placement row, absorb following non-epithet,
         non-noise orphans into uma_name (capped at
         `_MAX_FOLLOWUP_ABSORPTIONS`).

    Pre-placement noise that doesn't look like an epithet (header
    chrome, etc.) survives as its own non-placement row so the
    route layer can hide it under "Other detected text".
    """
    if not rows:
        return rows

    consumed: set[int] = set()
    # Pass 1 — epithet routing. Mutates target placement rows in place.
    for i, row in enumerate(rows):
        if row.get("placement") is not None:
            continue
        raw = (row.get("raw_line") or "").strip()
        if not _looks_like_epithet(raw):
            continue
        for j in range(i + 1, len(rows)):
            target = rows[j]
            if target.get("placement") is None:
                continue
            existing_name = (target.get("uma_name") or "").strip()
            target["uma_name"] = _tighten_punctuation(
                f"{raw} {existing_name}".strip() if existing_name else raw
            )
            target["raw_line"] = _tighten_punctuation(
                f"{raw} {(target.get('raw_line') or '').strip()}".strip()
            )
            consumed.add(i)
            break

    # Pass 2 — forward absorb non-epithet, non-noise orphans into the
    # preceding placement row. Stops at: next placement, epithet
    # (it's the next entrant's intro), end, or absorption cap.
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(rows):
        if i in consumed:
            i += 1
            continue
        cur = rows[i]
        if cur.get("placement") is None:
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
            if j in consumed:
                j += 1
                continue
            raw_j = (rows[j].get("raw_line") or "").strip()
            if _looks_like_epithet(raw_j):
                # Belongs to the next entrant — back off.
                break
            if raw_j and raw_j.lower() not in _NOISE_TOKENS:
                if merged.get("uma_name"):
                    merged["uma_name"] = _tighten_punctuation(
                        f"{merged['uma_name']} {raw_j}".strip()
                    )
                else:
                    merged["uma_name"] = _tighten_punctuation(raw_j)
                merged["raw_line"] = _tighten_punctuation(
                    f"{merged.get('raw_line', '')} {rows[j].get('raw_line', '')}".strip()
                )
            absorbed += 1
            j += 1
        out.append(merged)
        i = j if j > i + 1 else i + 1
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
