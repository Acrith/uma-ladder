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
from collections import defaultdict
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
# the Uma profile screen. Originally tuned for `Skills Inspiration
# Career Info` (Vision clusters all three tabs as one row). PR-OCR3:
# fall back to a standalone "Skills" line because Vision sometimes
# splits the tabs into separate paragraphs — in which case the
# stricter `skills + career` pattern fails and we'd return zero
# skills despite the section being right there.
_SKILLS_MARKER_FULL_RE = re.compile(
    r"\bskills?\b.*\bcareer\b", re.IGNORECASE
)
_SKILLS_MARKER_LONE_RE = re.compile(
    r"^\s*skills?\s*$", re.IGNORECASE
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


def _find_skills_marker(line_texts: Sequence[str]) -> int | None:
    """Locate the start of the post-Skills section. Tries the full
    `Skills … Career` cluster first (preferred — definitive when
    Vision clusters all three tabs on one row), falls back to a
    lone `Skills` line (handles the split-tabs case)."""
    for i, line in enumerate(line_texts):
        if _SKILLS_MARKER_FULL_RE.search(line):
            return i + 1
    for i, line in enumerate(line_texts):
        if _SKILLS_MARKER_LONE_RE.match(line):
            return i + 1
    return None


def _extract_skills_section_text(
    line_texts: Sequence[str],
) -> str:
    """Return the joined post-Skills, pre-Close text block."""
    start_idx = _find_skills_marker(line_texts)
    if start_idx is None:
        return ""
    end_idx = len(line_texts)
    for i in range(start_idx, len(line_texts)):
        # Stop at the first line that's ONLY an end token.
        tokens = line_texts[i].lower().split()
        if tokens and tokens[0] in _SKILLS_END_TOKENS:
            end_idx = i
            break
    block = " ".join(line_texts[start_idx:end_idx])
    # Strip level / circle noise BEFORE returning so the catalogue
    # scan doesn't have to know about them.
    return _SKILL_NOISE_RE.sub(" ", block)


def _extract_skills(
    line_texts: Sequence[str],
) -> list[dict[str, object]]:
    """Find every UmaSkill catalogue name occurring in the post-Skills
    text block. Returns one candidate dict per OCR occurrence,
    ordered by first occurrence (screen order). Each candidate:

    ::

        {
            "id": int,            # default catalog id (primary)
            "name_en": str,       # default name (e.g. "Right-Handed ◎")
            "raw_pos": int,       # position in normalized text block
            "is_inherited": bool, # True if this slot is a gene-version
                                  # (only meaningful for ults with both
                                  # innate + inherited catalog rows)
            "variants": [         # all catalog rows the OCR text could
                                  # resolve to — len > 1 when the
                                  # screenshot can't disambiguate
                {"id", "name_en", "is_inherited"}, ...
            ],
        }

    Two structural cases the extractor handles separately:

    - **Tier variants** (PR-OCR3): same normalized name, DIFFERENT
      ``name_en`` (e.g. ``Right-Handed ◎ / ○ / ×``). The OCR can't
      read the tier glyph reliably, so we emit ONE candidate with a
      multi-entry ``variants`` list and let the human pick in the
      confirm form's dropdown.
    - **Inherited dupes** (PR-OCR6): same ``name_en``, both
      ``is_inherited=False`` (innate unique) and ``is_inherited=True``
      (gene-version of the same ult) exist in the catalogue. Per the
      in-game rule "slot 0 is always the innate, later slots are
      inherited", the first OCR occurrence of the name picks the
      non-inherited row, subsequent occurrences pick the inherited
      row. Both surface in the confirm form so the organiser sees the
      full set.
    """
    block = _extract_skills_section_text(line_texts)
    if not block:
        return []
    norm_block = _normalize_for_skill_search(block)
    if not norm_block:
        return []
    # Load enabled skills once. ~2k rows is cheap. Order so
    # non-inherited rows come first within each catalog group — the
    # primary-pick logic below relies on this ordering.
    catalog = (
        db.session.query(UmaSkill)
        .filter(UmaSkill.enabled.is_(True))
        .order_by(UmaSkill.is_inherited.asc(), UmaSkill.id.asc())
        .all()
    )
    # Group by normalized name so all tier variants of "Right-Handed"
    # come out together at the same matched position.
    groups: dict[str, list[UmaSkill]] = defaultdict(list)
    for s in catalog:
        norm = _normalize_for_skill_search(s.name_en)
        if not norm or len(norm) < 3:
            # Skip degenerate short names that would false-positive
            # all over the place ("a", "go" etc).
            continue
        groups[norm].append(s)
    # Longest-first so "Long Corners" matches before "Long" if both
    # are in the catalogue — prevents a shorter prefix-skill from
    # cannibalising a longer one's text. Tie-break on name_en for
    # determinism.
    sorted_norms = sorted(
        groups.keys(),
        key=lambda n: (-len(n), n),
    )
    # Pass 1 — find every (position, norm_name) pair in the block,
    # marking spans consumed so a shorter prefix can't double-match.
    found: list[tuple[int, str]] = []
    consumed: list[bool] = [False] * len(norm_block)
    for norm_name in sorted_norms:
        start = 0
        while True:
            idx = norm_block.find(norm_name, start)
            if idx < 0:
                break
            if any(consumed[idx : idx + len(norm_name)]):
                start = idx + 1
                continue
            for k in range(idx, idx + len(norm_name)):
                consumed[k] = True
            found.append((idx, norm_name))
            start = idx + len(norm_name)

    # PR-OCR7 — Pass 2 (iterative-subtractive): build a "remaining
    # block" from positions NOT consumed by pass 1, and scan it
    # against the catalogue. Catches multi-row ult names whose
    # halves were visually wrapped onto separate rows in-game and
    # ended up split by an intervening catalog skill in Vision's
    # row-cluster join.
    #
    # Concrete: for the Tamamo Cross dump, pass 1 finds
    # "anchorsaweigh" at pos 19 of the block "whitelightningcomin
    # anchorsaweigh through" and consumes positions 19-31. The
    # remaining block becomes "whitelightningcominthrough" — and
    # "White Lightning Comin' Through!" now matches contiguously.
    #
    # Generalises to any "skill name visually split by another known
    # skill"; the pass is bounded (one extra catalogue scan) so the
    # cost is linear in catalog size, no recursion.
    remaining_chars: list[str] = []
    remaining_to_original: list[int] = []
    for i, ch in enumerate(norm_block):
        if not consumed[i]:
            remaining_chars.append(ch)
            remaining_to_original.append(i)
    remaining = "".join(remaining_chars)
    if remaining and len(remaining) >= 3:
        consumed_remaining = [False] * len(remaining)
        for norm_name in sorted_norms:
            start = 0
            while True:
                idx = remaining.find(norm_name, start)
                if idx < 0:
                    break
                if any(
                    consumed_remaining[idx : idx + len(norm_name)]
                ):
                    start = idx + 1
                    continue
                for k in range(idx, idx + len(norm_name)):
                    consumed_remaining[k] = True
                # Map back to the original position of the FIRST
                # character of the match so the screen-order sort
                # places the candidate correctly. The split-skill's
                # head was at the lower original position; using
                # that anchors the candidate before the intervening
                # consumed skill.
                orig_pos = remaining_to_original[idx]
                found.append((orig_pos, norm_name))
                start = idx + len(norm_name)

    found.sort()

    # Second pass — turn each match into a candidate dict.
    # Track per-name_en occurrence count so we can flag the first as
    # innate and subsequent as inherited for ults with both variants.
    name_occurrences: dict[str, int] = defaultdict(int)

    out: list[dict[str, object]] = []
    for pos, norm_name in found:
        group = groups[norm_name]
        distinct_names = {s.name_en for s in group}

        if len(distinct_names) > 1:
            # Tier-variant case (Right-Handed ◎ / ○ / ×). The OCR
            # text says "Right-Handed" — we don't know which glyph
            # was beside it. Collapse to one variant per name_en
            # (prefer non-inherited), sort by name_en for stable UI.
            by_name: dict[str, UmaSkill] = {}
            for s in group:
                cur = by_name.get(s.name_en)
                if cur is None or (cur.is_inherited and not s.is_inherited):
                    by_name[s.name_en] = s
            variant_list = sorted(by_name.values(), key=lambda x: x.name_en)
            primary = variant_list[0]
            out.append(
                {
                    "id": primary.id,
                    "name_en": primary.name_en,
                    "raw_pos": pos,
                    "is_inherited": primary.is_inherited,
                    "variants": [
                        {
                            "id": v.id,
                            "name_en": v.name_en,
                            "is_inherited": v.is_inherited,
                        }
                        for v in variant_list
                    ],
                }
            )
        else:
            # Single name_en — either a solo catalog entry or an
            # inherited-dupe (innate + gene). The position rule
            # decides which variant we surface.
            name_en = next(iter(distinct_names))
            non_inh = next((s for s in group if not s.is_inherited), None)
            inh = next((s for s in group if s.is_inherited), None)
            occ = name_occurrences[name_en]
            name_occurrences[name_en] = occ + 1
            if occ == 0 and non_inh is not None:
                picked = non_inh
            elif occ > 0 and inh is not None:
                picked = inh
            elif non_inh is not None:
                picked = non_inh
            else:
                # Catalogue only has the inherited variant. Rare;
                # surface it anyway so the skill at least appears.
                picked = inh  # type: ignore[assignment]
            if picked is None:
                continue
            out.append(
                {
                    "id": picked.id,
                    "name_en": picked.name_en,
                    "raw_pos": pos,
                    "is_inherited": picked.is_inherited,
                    "variants": [
                        {
                            "id": picked.id,
                            "name_en": picked.name_en,
                            "is_inherited": picked.is_inherited,
                        }
                    ],
                }
            )
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
    us partial data. `skills_block_debug` is the post-"Skills"
    text the parser actually scanned — exposed so the sandbox can
    show "here's exactly what we searched" when matches go wrong."""

    stats: dict[str, int] = field(default_factory=dict)
    aptitudes: dict[str, dict[str, str]] = field(default_factory=dict)
    skills: list[dict[str, object]] = field(default_factory=list)
    header: dict[str, object] = field(default_factory=dict)
    skills_block_debug: str = ""


def extract_uma_sheet(line_texts: Sequence[str]) -> UmaSheetExtract:
    return UmaSheetExtract(
        stats=_extract_stats_positional(line_texts),
        aptitudes=_extract_aptitudes(line_texts),
        skills=_extract_skills(line_texts),
        header=_extract_header(line_texts),
        skills_block_debug=_extract_skills_section_text(line_texts),
    )


# ─── Merging extracts from multiple screenshots ──────────────────


def merge_extracts(extracts: Sequence[UmaSheetExtract]) -> UmaSheetExtract:
    """Combine N extracts (one per screenshot) into a single view.

    Strategy:
    - Header / stats / aptitudes: first non-empty value per field
      wins. The user uploads screenshots in some order; assume the
      first one is the most complete header source.
    - Skills: union, deduped by skill id. The whole point of multi-
      screenshot uploads — first screen had skills 1-N, second had
      N+1-M, the merged view shows them all.
    - skills_block_debug: concatenate the per-screenshot blocks with
      `\n--- screenshot N ---\n` separators so the debug pane shows
      everything that contributed.
    """
    if not extracts:
        return UmaSheetExtract()

    header: dict[str, object] = {}
    stats: dict[str, int] = {}
    aptitudes: dict[str, dict[str, str]] = {}
    seen_skill_ids: set[int] = set()
    skills: list[dict[str, object]] = []
    debug_chunks: list[str] = []

    for i, e in enumerate(extracts, start=1):
        for k, v in e.header.items():
            header.setdefault(k, v)
        for k, v in e.stats.items():
            if v is not None:
                stats.setdefault(k, v)
        for cat, slots in e.aptitudes.items():
            cat_dest = aptitudes.setdefault(cat, {})
            for slot, rank in slots.items():
                cat_dest.setdefault(slot, rank)
        for s in e.skills:
            sid = s.get("id")
            if isinstance(sid, int) and sid not in seen_skill_ids:
                seen_skill_ids.add(sid)
                skills.append(s)
        if e.skills_block_debug:
            debug_chunks.append(
                f"--- screenshot {i} ---\n{e.skills_block_debug}"
            )

    return UmaSheetExtract(
        stats=stats,
        aptitudes=aptitudes,
        skills=skills,
        header=header,
        skills_block_debug="\n\n".join(debug_chunks),
    )
