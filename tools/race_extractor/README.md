# uma-race-extract

Saves the Room Match result you just ran and sends it to
[umaladder.moe](https://umaladder.moe) — every participant's uma,
stats, aptitudes and skills, plus the finishing order, times and
margins.

Reads the game's memory. It does not write to the game, does not click
anything, and does not change how the game behaves.

## For players (Windows)

1. Finish a Room Match — or open a saved result / replay
2. Double-click **`uma-race-extract.exe`**
3. A JSON file appears in `captures/` next to the exe

That's it. No Python, no command line.

The saved result stays in memory after you leave the result screen, so
you don't have to sit on a particular screen. **If a replay is open,
the full race telemetry is captured too** — position, speed and stamina
for every runner across the whole race.

**If the game isn't running,** the recorder waits up to 5 minutes for
you to start it.

## Uploading

On first run it asks once for an upload token and remembers the answer
either way — declining is remembered too, so it won't nag.

Get a token from your account page on umaladder.moe. Settings live in
`uma-race-config.json` beside the exe:

```json
{ "api_url": "https://umaladder.moe", "token": "..." }
```

> **This is umaladder.moe, not training.umaladder.moe.** The IT run
> recorder is a different tool for a different site and keeps its
> settings in `uma-it-config.json`. The two never read each other's
> config, and a token from one will not work on the other.

**The local JSON is always written before any upload is attempted**, so
a network failure can never cost you a race. Nothing is uploaded at all
if you don't set a token.

Uploading the same race twice is fine — the site recognises it. If
several people in one room each run the recorder, the first upload
wins and the rest are recognised as duplicates.

## What lands on the site

A capture arrives as **pending**. It does not touch the ladder until a
human reviews and confirms it — the same rule the screenshot flow has
always had.

## Running from source

```
py -3 uma_race_extract.py            # capture + upload
py -3 uma_race_extract.py --no-upload
```

Needs `frida`, and `vendor/il2cpp_bridge.js` beside the script.

## How it works

`Gallop.WorkRoomMatchData._savedRaceResultInfo` holds the saved result.
The finishing order lives in an obscured, gzipped blob that
`scenario_decode.py` unpacks; when a replay is loaded the game's own
parsed `RaceSimulateData` is read instead, which also yields per-frame
telemetry. Full write-up: [`docs/room-match-extraction.md`](../../docs/room-match-extraction.md).
