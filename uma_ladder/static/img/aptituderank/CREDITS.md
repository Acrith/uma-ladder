# Aptitude rank icon assets

Source: <https://github.com/kachi-dev/uma-tools/tree/master/icons>
(the `utx_ico_statusrank_NN.png` files at the root of that
`icons/` directory).

These 8 PNGs map directly to the in-game aptitude grades used for
Track / Distance / Style aptitudes on the Uma profile sheet:

| Filename                         | Grade | Notes                |
| -------------------------------- | ----- | -------------------- |
| `utx_ico_statusrank_00.png`      | G     | Lowest aptitude.     |
| `utx_ico_statusrank_01.png`      | F     |                      |
| `utx_ico_statusrank_02.png`      | E     |                      |
| `utx_ico_statusrank_03.png`      | D     |                      |
| `utx_ico_statusrank_04.png`      | C     |                      |
| `utx_ico_statusrank_05.png`      | B     |                      |
| `utx_ico_statusrank_06.png`      | A     |                      |
| `utx_ico_statusrank_07.png`      | S     | Best.                |

The mapping logic (Python in
`uma_ladder/services/stat_ranks.py::aptitude_grade_icon_filename`)
mirrors kachi-dev's JSX:

```js
const APTITUDES = Object.freeze(['S','A','B','C','D','E','F','G']);
const idx = 7 - APTITUDES.indexOf(grade);
// → utx_ico_statusrank_<idx zero-padded to 2>.png
```

Game-mechanic mapping (grade → index) is a published threshold,
not a copyrightable expression; we re-derive it here in Python.
The image assets themselves are Cygames game art redistributed
under the same community-tool posture as the stat-rank icons in
the sibling `statusrank/` directory — see that directory's
CREDITS.md for the asset-redistribution context.

Aptitude grades only span G — S (8 buckets). There's no "+" tier
for aptitudes in-game (unlike stat ranks, which go up to SS+).
