# Stat-rank icon assets

Source: <https://github.com/kachi-dev/uma-tools/tree/master/icons/statusrank>

These 98 PNG icons (`ui_statusrank_00.png` through
`ui_statusrank_97.png`) are extracted from the *Umamusume: Pretty
Derby* client (Cygames, Inc.) and are redistributed by the
[kachi-dev/uma-tools](https://github.com/kachi-dev/uma-tools)
community tooling project under their permissive community-use
posture. Uma Ladder mirrors that posture: the assets are used here
for community / educational purposes only; original ownership
remains with Cygames.

The rank-from-value MAPPING (Python logic in
`uma_ladder/services/stat_ranks.py`) was reimplemented from
scratch using the same numeric thresholds as kachi-dev's
`rankForStat` function (game-mechanic formulas are not
copyrightable). The kachi-dev source code is GPL-3; only the
threshold values were referenced, not the code itself.

Image filenames map to a 0-97 rank index:
- `00`-`17`: G / G+ / F / F+ / E / E+ / D / D+ / C / C+ / B / B+ /
  A / A+ / S / S+ / SS / SS+ (the typical 1-1200 stat range)
- `18`-`97`: UG bracket variants for raw values > 1200 (rare in
  normal play; kachi's formula spreads them by 100s with +10
  minor increments)
