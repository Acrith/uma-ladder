# Season text-glyph assets

Source: <https://github.com/kachi-dev/uma-tools/tree/master/icons>
(the `utx_txt_season_NN.png` files).

Mapping mirrors the `Season` enum in kachi-dev's RaceParameters,
excluding `Sakura` (5th value) which our `RaceSeason` enum doesn't
support yet:

| Filename                       | Season |
| ------------------------------ | ------ |
| `utx_txt_season_00.png`        | Spring |
| `utx_txt_season_01.png`        | Summer |
| `utx_txt_season_02.png`        | Autumn |
| `utx_txt_season_03.png`        | Winter |
| `utx_txt_season_04.png`        | Sakura (not used here) |

These are TEXT-style glyphs (the season name in the in-game
season-styled font), not icon-style. Wider than they are tall; size
with `h-5 w-auto` or similar.

Game art redistributed under the same community-tool posture as
the sibling icon directories.
