"""PR-A2 — stat-rank mapping for Speed / Stamina / Power / Guts / Wit.

The in-game uma profile sheet displays a rank glyph (G, G+, F, …,
SS+, UG) next to each stat value. The mapping from numeric value
to rank glyph follows fixed thresholds; we replicate them here so
the per-result confirm page + race detail card can render the
right icon without OCR'ing the glyph (Vision can't read it
reliably from screenshots).

Formula mirrors kachi-dev/uma-tools' ``rankForStat`` — same
thresholds, reimplemented in Python. The function returns an
integer index 0-97 that maps directly to a PNG filename:
``static/img/statusrank/ui_statusrank_<idx:02d>.png``.

Rank table (the typical 1-1200 range):

    idx | label | value range
    ----|-------|-------------
    0   | G     | 1-49
    1   | G+    | 50-99
    2   | F     | 100-149
    3   | F+    | 150-199
    4   | E     | 200-249
    5   | E+    | 250-299
    6   | D     | 300-349
    7   | D+    | 350-399
    8   | C     | 400-499
    9   | C+    | 500-599
    10  | B     | 600-699
    11  | B+    | 700-799
    12  | A     | 800-899
    13  | A+    | 900-999
    14  | S     | 1000-1099
    15  | S+    | (unused — game jumps S → SS at 1100)
    16  | SS    | 1100-1149
    17  | SS+   | 1150-1200

Beyond 1200 the formula produces a "UG" bracket (indices 18+); we
keep the icon mapping but collapse the label to a generic "UG" for
human display since raw stats over 1200 are rare in normal play.
"""

from __future__ import annotations

# Standard labels for indices 0-17. Index 15 ("S+") is never
# produced by the kachi formula — the game jumps from S (1000-1099)
# straight to SS (1100+) — so the entry is present for icon-index
# alignment but never returned by `stat_rank_label`.
_LABELS: tuple[str, ...] = (
    "G", "G+",
    "F", "F+",
    "E", "E+",
    "D", "D+",
    "C", "C+",
    "B", "B+",
    "A", "A+",
    "S", "S+",
    "SS", "SS+",
)


def stat_rank_index(value: int | None) -> int | None:
    """Return the 0-97 status-rank index for ``value``.

    None / 0 / negative values produce None (no rank — render an
    em-dash placeholder upstream). The returned int is also the
    icon filename suffix: ``ui_statusrank_<idx:02d>.png``.
    """
    if value is None or value < 1:
        return None
    if value > 1200:
        # UG bracket — letter increments every 100, minor variant
        # increments every 10. Capped at 97 (last icon kachi ships).
        idx = 18 + ((value - 1200) // 100) * 10 + (value // 10) % 10
        return min(idx, 97)
    if value >= 1150:
        return 17  # SS+
    if value >= 1100:
        return 16  # SS  (note: formula skips index 15 = S+)
    if value >= 400:
        return 8 + (value - 400) // 100
    return value // 50


def stat_rank_label(value: int | None) -> str:
    """Human-readable rank label ("SS+", "B", "G+", …) for ``value``.
    UG bracket variants (indices 18+) collapse to the generic
    ``"UG"`` since the game-internal sub-variants aren't useful in
    a results context."""
    idx = stat_rank_index(value)
    if idx is None:
        return ""
    if idx < len(_LABELS):
        return _LABELS[idx]
    return "UG"


def stat_rank_icon_filename(value: int | None) -> str | None:
    """Filename (no path) of the PNG asset for ``value``. None when
    the value doesn't have a rank. Caller resolves to a static URL
    via ``url_for('static', filename='img/statusrank/' + filename)``
    or equivalent."""
    idx = stat_rank_index(value)
    if idx is None:
        return None
    return f"ui_statusrank_{idx:02d}.png"
