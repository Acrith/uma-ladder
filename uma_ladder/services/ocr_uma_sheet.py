"""PR-OCR2 — sheet-specific extraction for the in-game Uma profile.

Distinct from services.ocr_google_vision._extract_stats /
_extract_skill_candidates because the profile screen has a layout
the race-result parser was never tuned for:

- **Stats**: all 5 labels on ONE row, values on the NEXT row,
  positional match. The race-result extractor breaks here because
  it grabs the first int after each label — the same first number
  ends up assigned to every stat.
- **Aptitudes**: shown as `<Category> <Name> <Rank>` triples on
  three rows (Track / Distance / Style). Race-result extractor
  doesn't know about these at all.
- **Skills**: come AFTER a "Skills … Career Info" tab marker, and
  live in a single block where Vision merges multiple skills per
  row and splits long names ("White Lightning Comin' / Through!")
  across rows. The race-result extractor returns each row as a
  standalone candidate which can't survive either failure mode.
- **Header**: Uma name, outfit (in brackets), epithet, uma score,
  trainer name. Conservative parse — when in doubt leave blank.

Input is the pre-clustered `line_texts` produced by
`services.ocr_google_vision._parse_annotation` (one string per
visual row from Vision's clustering pass). Output is a flat
dataclass — every field independently optional, so a layout
change still gives us partial data.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..extensions import db
from ..models import UmaSkill

# ─── Stats ───────────────────────────────────────────────────────

# Canonical game order — Speed → Stamina → Power → Guts → Wisdom.
_STAT_ORDER: tuple[str, ...] = (
    "speed",
    "stamina",
    "power",
    "guts",
    "wisdom",
)

# Label variants. "Wit" is the JP-style short form that Vision often
# reads instead of "Wisdom". Both map to the same field.
_STAT_LABEL_VARIANTS: dict[str, tuple[str, ...]] = {
    "speed": ("speed",),
    "stamina": ("stamina",),
    "power": ("power",),
    "guts": ("guts",),
    "wisdom": ("wisdom", "wit"),
}

# In-game stats cap around 1200 raw, can go higher with bonuses but
# never into 5-digit territory. The 4-digit ceiling keeps us from
# absorbing the uma score (17,307) accidentally.
_STAT_NUMBER_RE = re.compile(r"\b(\d{2,4})\b")


def _find_stat_labels_row(line: str) -> list[tuple[int, str]]:
    """If the row contains all 5 stat labels in canonical order,
    return [(left_x, stat_name), ...]; else []."""
    lower = line.lower()
    positions: list[tuple[int, str]] = []
    for stat in _STAT_ORDER:
        # Find the leftmost variant present.
        best: int | None = None
        for variant in _STAT_LABEL_VARIANTS[stat]:
            idx = lower.find(variant)
            if idx >= 0 and (best is None or idx < best):
                best = idx
        if best is None:
            return []  # missing label — not the stats row
        positions.append((best, stat))
    # Verify left-to-right order matches canonical stat order.
    if [s for _, s in sorted(positions)] != list(_STAT_ORDER):
        return []
    return sorted(positions)


def _extract_stats_positional(
    line_texts: Sequence[str],
) -> dict[str, int]:
    """Find a row matching all 5 labels in order, then map the 5
    numbers from the following row index-to-index."""
    for i, line in enumerate(line_texts):
        labels = _find_stat_labels_row(line)
        if not labels:
            continue
        # Scan the next few lines for the value row — sometimes there's
        # an intervening rank row ("S+ A+ A+ B B") before the numbers.
        # Cap at +3 to avoid wandering into aptitudes.
        for j in range(i + 1, min(i + 4, len(line_texts))):
            nums = _STAT_NUMBER_RE.findall(line_texts[j])
            if len(nums) < 5:
                continue
            ints = [int(n) for n in nums[:5]]
            return {stat: ints[k] for k, (_, stat) in enumerate(labels)}
        return {}
    return {}


# ─── Aptitudes ───────────────────────────────────────────────────

# `track`, `distance`, `style` are the three aptitude categories on
# the profile screen. Within each category, a fixed set of named
# slots holds a rank letter.
_APT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "track": ("Turf", "Dirt"),
    "distance": ("Sprint", "Mile", "Medium", "Long"),
    "style": ("Front", "Pace", "Late", "End"),
}

# Aptitude ranks span G..S (8 letters). Stat / overall ranks add SS,
# S+, etc — broader regex below covers both surfaces.
_RANK_RE = re.compile(
    r"^(SS\+|SS|S\+|S|A\+|A|B\+|B|C\+|C|D\+|D|E\+|E|F\+|F|G\+|G|UG\d*)$",
    re.IGNORECASE,
)


def _extract_aptitudes(
    line_texts: Sequence[str],
) -> dict[str, dict[str, str]]:
    """Parse the three aptitude rows. Each row starts with its
    category keyword (Track / Distance / Style) followed by
    alternating ``<Name> <Rank>`` pairs. Order-tolerant within the
    row — if Vision injects a stray token we skip it and keep
    pairing rather than abandoning the row."""
    out: dict[str, dict[str, str]] = {}
    for line in line_texts:
        tokens = line.split()
        if not tokens:
            continue
        category = tokens[0].lower()
        if category not in _APT_CATEGORIES:
            continue
        valid_names = {n.lower() for n in _APT_CATEGORIES[category]}
        cat_out: dict[str, str] = {}
        i = 1
        while i + 1 < len(tokens):
            name = tokens[i]
            rank = tokens[i + 1]
            if name.lower() in valid_names and _RANK_RE.match(rank):
                cat_out[name.lower()] = rank.upper()
                i += 2
            else:
                # Stray glyph from Vision (a stray "♥", say) — skip
                # one token and retry the pair walk rather than
                # giving up on the whole row.
                i += 1
        if cat_out:
            out[category] = cat_out
    return out


# ─── Skills ──────────────────────────────────────────────────────

# Tab-bar marker that demarcates the start of the skills section on
# the Uma profile screen. Vision reads it as one row containing all
# three tab labels.
_SKILLS_MARKER_RE = re.compile(
    r"\bskills?\b.*\bcareer\b", re.IGNORECASE
)

# End markers. "Close" is the dialog dismiss button; "Save As" /
# "Partner" are the "Save As Practice Partner" button text rendered
# above the tabs and shouldn't appear AFTER the skills section, but
# we defensively stop at any of them.
_SKILLS_END_TOKENS: frozenset[str] = frozenset(
    {"close", "save", "partner"}
)

# Noise tokens that show up inside the skills block as decorations
# (level indicators, circle markers, etc.) — stripped before the
# catalogue search.
_SKILL_NOISE_RE = re.compile(
    r"\b(lvl|level)\s*\d+\b|☑|◎|○|●|■|[★☆]+|\bO+\b",
    re.IGNORECASE,
)

# Same normalization as services.official._normalize_skill_name —
# the matcher there strips everything except [a-z0-9]. Mirroring
# the rule lets us drop punctuation / spacing artifacts uniformly.
_SKILL_NAME_NOISE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_for_skill_search(s: str) -> str:
    return _SKILL_NAME_NOISE_RE.sub("", s.lower())


def _extract_skills_section_text(
    line_texts: Sequence[str],
) -> str:
    """Return the joined post-Skills, pre-Close text block."""
    start_idx: int | None = None
    end_idx = len(line_texts)
    for i, line in enumerate(line_texts):
        if start_idx is None and _SKILLS_MARKER_RE.search(line):
            start_idx = i + 1
            continue
        if start_idx is not None:
            # Stop at the first line that's ONLY an end token.
            tokens = line.lower().split()
            if tokens and tokens[0] in _SKILLS_END_TOKENS:
                end_idx = i
                break
    if start_idx is None:
        return ""
    block = " ".join(line_texts[start_idx:end_idx])
    # Strip level / circle noise BEFORE returning so the catalogue
    # scan doesn't have to know about them.
    return _SKILL_NOISE_RE.sub(" ", block)


def _extract_skills(
    line_texts: Sequence[str],
) -> list[dict[str, object]]:
    """Find every UmaSkill catalogue name occurring in the post-Skills
    text block. Returns [{"id": int, "name_en": str, "raw_pos": int}]
    ordered by first occurrence so the UI lists them in screen order."""
    block = _extract_skills_section_text(line_texts)
    if not block:
        return []
    norm_block = _normalize_for_skill_search(block)
    if not norm_block:
        return []
    # Load enabled skills once. ~2k rows is cheap.
    catalog = db.session.query(UmaSkill).filter(UmaSkill.enabled.is_(True)).all()
    # Sort longest-first so "Long Corners" matches before "Long" if
    # both were ever in the catalogue. Prevents a shorter substring
    # skill from cannibalising a longer one's text.
    catalog_norm = sorted(
        (
            (_normalize_for_skill_search(s.name_en), s.id, s.name_en)
            for s in catalog
        ),
        key=lambda t: (-len(t[0]), t[2]),
    )
    found: list[tuple[int, int, str]] = []
    consumed: list[bool] = [False] * len(norm_block)
    for norm_name, skill_id, name_en in catalog_norm:
        if not norm_name or len(norm_name) < 3:
            # Skip degenerate short names that would false-positive
            # all over the place.
            continue
        # Search every occurrence; on each hit, mark that span as
        # consumed so a shorter prefix-skill can't claim the same
        # characters.
        start = 0
        while True:
            idx = norm_block.find(norm_name, start)
            if idx < 0:
                break
            span = consumed[idx : idx + len(norm_name)]
            if any(span):
                start = idx + 1
                continue
            for k in range(idx, idx + len(norm_name)):
                consumed[k] = True
            found.append((idx, skill_id, name_en))
            start = idx + len(norm_name)
    found.sort()
    # Dedupe by skill_id, preserving order of first occurrence.
    seen: set[int] = set()
    out: list[dict[str, object]] = []
    for pos, sid, name_en in found:
        if sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "name_en": name_en, "raw_pos": pos})
    return out


# ─── Header (uma name, outfit, epithet, score, trainer) ──────────

# "[Outfit Name]" appears next to the rank stripe at the top of the
# sheet. Brackets are sometimes Vision-tokenised with spaces inside.
_OUTFIT_RE = re.compile(r"\[\s*([^\]]+?)\s*\]")

# Uma score is rendered as a comma-thousands integer, e.g. "17,307"
# or "1,234". We don't fall back to a bare 4+ digit int because that
# overlaps stat values.
_SCORE_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+)\b")

# "Trainer <Name>" or "Trainer: <Name>".
_TRAINER_RE = re.compile(
    r"\btrainer\b[:\s]+([A-Za-z][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

# "RANK <Name>" — Vision often picks up the in-game rank badge text
# as "RANK" preceding the uma name. We treat the rest of the row
# as the uma name.
_UMA_NAME_AFTER_RANK_RE = re.compile(
    r"^RANK\s+(.+?)$", re.IGNORECASE
)


def _extract_header(line_texts: Sequence[str]) -> dict[str, object]:
    """Pull whatever recognisable header fields we can. Each is
    independently optional."""
    out: dict[str, object] = {}
    for i, line in enumerate(line_texts):
        # Outfit (bracket content).
        if "outfit" not in out:
            m = _OUTFIT_RE.search(line)
            if m:
                out["outfit"] = m.group(1).strip()

        # Uma name from a "RANK <Name>" row.
        if "uma_name" not in out:
            m = _UMA_NAME_AFTER_RANK_RE.match(line.strip())
            if m:
                name = m.group(1).strip()
                # Filter ALL-CAPS noise rows ("RANK A" / "RANK SS+").
                if name and not _RANK_RE.match(name):
                    out["uma_name"] = name

        # Uma score (comma-thousands integer).
        if "uma_score" not in out:
            m = _SCORE_RE.search(line)
            if m:
                with contextlib.suppress(ValueError):
                    out["uma_score"] = int(m.group(1).replace(",", ""))

        # Trainer name.
        if "trainer_name" not in out:
            m = _TRAINER_RE.search(line)
            if m:
                out["trainer_name"] = m.group(1)

        # Epithet — the row immediately following a row that is ONLY
        # "Epithet" (the in-game label).
        if (
            "epithet" not in out
            and line.strip().lower() == "epithet"
            and i + 1 < len(line_texts)
        ):
            candidate = line_texts[i + 1].strip()
            if candidate:
                out["epithet"] = candidate

    return out


# ─── Public API ──────────────────────────────────────────────────


@dataclass(frozen=True)
class UmaSheetExtract:
    """Structured extraction from an Uma profile screenshot.

    Every field is independently optional; the sandbox renders
    em-dashes for any missing piece so a layout change still gives
    us partial data."""

    stats: dict[str, int] = field(default_factory=dict)
    aptitudes: dict[str, dict[str, str]] = field(default_factory=dict)
    skills: list[dict[str, object]] = field(default_factory=list)
    header: dict[str, object] = field(default_factory=dict)


def extract_uma_sheet(line_texts: Sequence[str]) -> UmaSheetExtract:
    return UmaSheetExtract(
        stats=_extract_stats_positional(line_texts),
        aptitudes=_extract_aptitudes(line_texts),
        skills=_extract_skills(line_texts),
        header=_extract_header(line_texts),
    )
