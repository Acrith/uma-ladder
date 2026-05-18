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


# ─── Aptitude grade icons ────────────────────────────────────────
#
# Aptitudes (Track / Distance / Style slots on the uma profile)
# span G through S — 8 grades, no "+" tier. Mapping mirrors kachi-
# dev/uma-tools' JSX (game-mechanic mapping, not a copyrightable
# expression):
#
#     APTITUDES = ['S','A','B','C','D','E','F','G']
#     idx = 7 - APTITUDES.indexOf(grade)
#     filename = `utx_ico_statusrank_${idx zero-padded to 2}.png`
#
# So G→00, F→01, ..., S→07.

_APTITUDE_GRADES: tuple[str, ...] = (
    "G", "F", "E", "D", "C", "B", "A", "S",
)


def aptitude_grade_icon_filename(grade: str | None) -> str | None:
    """PNG filename for a Track/Distance/Style aptitude grade.

    Returns None for empty / unknown grades — caller renders an
    em-dash placeholder. Case-insensitive."""
    if not grade:
        return None
    g = grade.strip().upper()
    try:
        idx = _APTITUDE_GRADES.index(g)
    except ValueError:
        return None
    return f"utx_ico_statusrank_{idx:02d}.png"


# ─── Weather + season icons ──────────────────────────────────────
#
# Filename → meaning mirrors kachi-dev's RaceParameters enums:
#   Weather: Sunny=00, Cloudy=01, Rainy=02, Snowy=03
#   Season:  Spring=00, Summer=01, Autumn=02, Winter=03
# (Sakura=04 exists upstream but our RaceSeason enum doesn't ship it.)

_WEATHER_ORDER: tuple[str, ...] = ("Sunny", "Cloudy", "Rainy", "Snowy")
_SEASON_ORDER: tuple[str, ...] = ("Spring", "Summer", "Autumn", "Winter")


def weather_icon_filename(weather: str | None) -> str | None:
    """PNG filename for a Weather string. None for unknown / empty."""
    if not weather:
        return None
    try:
        idx = _WEATHER_ORDER.index(weather.strip().title())
    except ValueError:
        return None
    return f"utx_ico_weather_{idx:02d}.png"


def season_icon_filename(season: str | None) -> str | None:
    """PNG filename (text-glyph style) for a RaceSeason string."""
    if not season:
        return None
    try:
        idx = _SEASON_ORDER.index(season.strip().title())
    except ValueError:
        return None
    return f"utx_txt_season_{idx:02d}.png"


# ─── PR-A6: overall uma rank from rank points (uma_score) ────────
#
# The in-game uma sheet displays an overall rank glyph (G..SS+..Ug⁶)
# next to the uma name. Source data is the "uma score" integer also
# visible on the sheet (e.g. 17,307). Threshold table is the
# published one (sourced from user-provided Excel formula); ranks
# G through SS+ reuse the existing stat-rank icon set (00..17),
# Ug⁰..Ug⁶ map to icon positions 18..24 (also in the downloaded set).

# Strict upper bounds → label. Order matters (ascending).
_RANK_POINTS_TABLE: tuple[tuple[int, str], ...] = (
    (300, "G"),
    (600, "G+"),
    (900, "F"),
    (1300, "F+"),
    (1800, "E"),
    (2300, "E+"),
    (2900, "D"),
    (3500, "D+"),
    (4900, "C"),
    (6500, "C+"),
    (8200, "B"),
    (10000, "B+"),
    (12100, "A"),
    (14500, "A+"),
    (15900, "S"),
    (17500, "S+"),
    (19200, "SS"),
    (19600, "SS+"),
    (20000, "Ug⁰"),
    (20400, "Ug¹"),
    (20800, "Ug²"),
    (21200, "Ug³"),
    (21600, "Ug⁴"),
    (22100, "Ug⁵"),
    # Anything ≥ 22100 falls through to the last bucket below.
)
_RANK_POINTS_TOP_LABEL = "Ug⁶"


def rank_points_index(score: int | None) -> int | None:
    """0-indexed rank bucket for ``score`` per the published
    threshold table. None when score is None or non-positive."""
    if score is None or score <= 0:
        return None
    for idx, (upper, _label) in enumerate(_RANK_POINTS_TABLE):
        if score < upper:
            return idx
    return len(_RANK_POINTS_TABLE)  # Ug⁶ = top bucket


def rank_points_label(score: int | None) -> str:
    """Human-readable label ("SS+", "Ug⁴", …) for ``score``."""
    idx = rank_points_index(score)
    if idx is None:
        return ""
    if idx < len(_RANK_POINTS_TABLE):
        return _RANK_POINTS_TABLE[idx][1]
    return _RANK_POINTS_TOP_LABEL


def rank_points_icon_filename(score: int | None) -> str | None:
    """PNG filename in `static/img/statusrank/` for ``score``.
    Reuses the stat-rank icon set: indices 0-17 are the G..SS+
    icons; indices 18-24 are the Ug⁰..Ug⁶ icons (kachi-dev's
    statusrank set ships all 25 used here)."""
    idx = rank_points_index(score)
    if idx is None:
        return None
    return f"ui_statusrank_{idx:02d}.png"
