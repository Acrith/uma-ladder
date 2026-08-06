# Vendored reference files

## hakuraku_RaceDataParser.ts

Snapshot of `src/data/RaceDataParser.ts` from
[github.com/ayaliz/hakuraku](https://github.com/ayaliz/hakuraku)
(MIT license), fetched 2026-08-06.

This is the complete scenario-blob layout — header, frame block
(f32 time + per-horse 12-byte packed records), 31-byte result
records, sized event records — which we verified byte-for-byte
against our own captures (see `verify_blob_layout.py` beside this
directory and the "blob layout is public" section of
`docs/room-match-extraction.md`).

Kept as the authoritative reference for porting the frame/event
parse into `tools/race_extractor/scenario_decode.py`. Includes their
JP-format fallback parser (39-byte results, more event types) that
Global will likely need eventually.
