# Room Match extraction — memory-scan alternative to OCR result submission

Reference for a Frida + IL2CPP memory-read pipeline that captures a
complete Room Match result from the game client — every participant's
uma + skills + factors + stats + the race outcome — in a single click,
no screenshots, no OCR.

Captured live 2026-08-04 from a real 11-participant Room Match result
screen (InyanyaCup ?1, race_instance_id 800095). Full schema + sample
data + reproducible scout scripts saved under
[`room-match-scout/`](room-match-scout/).

## Why this matters for Uma Ladder

Uma Ladder's current result-submission path is OCR-assisted:
screenshot → OCR → human confirms placements/stats/skills → save. This
works but is slow, error-prone, and only extracts what's visible on
one screen.

Memory extraction replaces that pipeline entirely:

- **One capture** = every participant's full build (11–12 rows), the
  room settings, and the race outcome — data OCR literally cannot see
  because it's not on the result screen at all (e.g., every runner's
  distance/style/ground aptitude, exact stats, entire 20-slot skill
  array with levels, factors).
- **Structured, not fuzzy** — integer IDs (skill_id, chara_id,
  card_id) instead of OCR'd text. Zero confirmation-UI needed.
- **Same or higher fidelity than the game's own Race Result Detail
  dialog** because we walk the source-of-truth game object.

Two clean use cases inside Uma Ladder:

1. **Draft PvP result submission.** After the two players finish
   their match, the host clicks "Capture" in a small helper app; JSON
   uploads to Uma Ladder; the app pre-fills placements + Elo change.
   Human still confirms before it hits the ladder (per PROJECT_INTENTIONS
   §5.2 — "Human confirms/corrects result").
2. **Official race result submission.** Organizer runs the same
   helper after the official race concludes; ladder gets a rich
   result row with per-runner detail auto-attached.

Both replace the screenshot-upload step; both keep the human-confirm
gate the project's design already requires.

## Cost, risk, and dependencies

Not free — this is a separate cross-repo effort:

- **Requires a Windows helper app the user runs alongside Umamusume**
  (Frida attaches to the game process). It's the same shape as the
  already-shipping [uma-it-optimizer](https://github.com/Acrith/uma-it-optimizer)
  extractor. That project's `.exe` extractor + IL2CPP scaffolding
  can be reused wholesale — this doc's next-steps section calls out
  which files to fork from.
- **Anti-cheat exposure is small but non-zero.** The capture is a
  purely passive memory read (no method hooks, no writes) taken while
  the user sits on a static result screen — the lowest-risk shape of
  the Frida-attach patterns we've validated in the sibling project.
  Prior scouts documented three failure modes to avoid; the mitigations
  are in the sibling project's memory notes.
- **Not all users can/will install a helper app.** OCR submission
  stays as the fallback. This becomes an *optional accelerator*, not
  a replacement.

## The find

`Gallop.WorkRoomMatchData._savedRaceResultInfo` (a
`Gallop.WorkRoomMatchData.SavedRaceResultData` instance) is live in
IL2CPP memory while the user sits on the Room Match result screen. It
contains three things:

- Room metadata (`RoomMatchSavedRoomInfo`) — track/weather/seed/etc.
- Per-participant data (`RaceHorseData[]`) — full uma builds
- Encrypted race scenario (`ObscuredString`, ~31 KB) — the replay
  data with per-frame positions + skill activations

The msgpack response objects
(`Gallop.RoomMatchRaceEndResultResponse`,
`Gallop.RoomMatchGetSavedRaceResultResponse`) are freed after
deserialization — 0 live instances at capture time. `WorkRoomMatchData`
is the game's persistent canonical model built from the response, and
that's what we walk.

## Full schema (walked from the live capture)

### `WorkRoomMatchData` (singleton)

Notable fields on the root object:

```
._raceResultInfo         [RaceResultData]      = null after screen close
._savedRaceResultInfo    [SavedRaceResultData] = LIVE — this is our target
._currentRoomData        [RoomData]            = live room settings
._currentRoomUserList    [List<UserData>]      = per-player metadata
._currentRoomJoinUserList / _currentRoomLeaveUserList
._roomMatchEntryCharaIdArray  — ids of your entered chara(s)
._isReplay               [ObscuredBool]
._deckDict               [Dict<int, RacePresetData>] — saved deck presets
```

### `SavedRaceResultData`

```
._raceResult          [RoomMatchSavedRoomInfo]        — room metadata
._raceHorseDataArray  [RaceHorseData[]]               — 11-12 runners
._raceScenario        [ObscuredString, ~31 KB]        — encrypted replay
```

### `RoomMatchSavedRoomInfo` (room metadata)

Live capture values shown as examples.

```
.saved_room_id                   Int32     34408987
.register_id                     UInt64    <obscured>
.host_viewer_id                  Int64     <obscured>
.race_instance_id                Int32     800095       — lookup in race_data master → track/distance
.room_name                       string    "InyanyaCup ?1"
.message                         string    "May the best Umamusume win!"
.season                          Int32     2
.weather                         Int32     1
.ground_condition                Int32     1
.motivation                      Int32     5
.entry_num                       Int32     11
.current_entry_num               Int32     11
.private_entry_type              Int32     0
.private_entry_num               Int32     0
.private_current_entry_num       Int32     0
.is_allow_watching               Int32     1
.trained_chara_restriction       Int32     0
.restriction_type                Int32     0
.start_time                      string    "2026-07-26 23:30:45"
.random_seed                     Int32     1973968693
.favorite_flag                   Int32     0
.own_join_type                   Int32     2
.restrict_chara_info_array       RestrictCharaInfo[]   len=0 here
```

`race_instance_id` → look up in `race_data` master for
track / distance / turn direction / distance category. Uma Ladder
should either ship a race_data seed (from a GameTora import — see
PROJECT_INTENTIONS §2.9 on external data attribution) or fetch it
lazily via uma.moe's exposure of the same table.

### `RaceHorseData` (per participant, ×11 in the sample capture)

```
.viewer_id                          Int64     <obscured>   — player account id (game-internal, not GameTora)
.owner_viewer_id                    Int64     <obscured>   — set if the uma was rented
.trainer_name                       string    "Trubber"    — real player display name
.owner_trainer_name                 string    ""           — populated if rented
.single_mode_chara_id               Int32     1290         — id of their trained IT uma
.trained_chara_id                   Int32     1290         — same, in this capture
.nickname_id                        Int32     236          — player title/nickname
.card_id                            Int32     100402       — uma card id
.chara_id                           Int32     1004         — base uma id (masters lookup)
.rarity                             Int32     4            — 3/4/5-star
.talent_level                       Int32     5            — unique-skill level
.frame_order                        Int32     1            — starting gate

.skill_array                        SkillData[]  len up to 24
   .skill_id                        Int32     e.g. 110041  — masters lookup for name
   .level                           Int32     1..5

.stamina                            Int32     492
.speed                              Int32     1571
.pow                                Int32     1116
.guts                               Int32     532
.wiz                                Int32     1177

.running_style                      Int32     1            — 1=nige 2=senko 3=sashi 4=oikomi
.race_dress_id                      Int32     100430       — costume
.chara_color_type                   Int32     0
.npc_type                           Int32     11
.final_grade                        Int32     25           — game grade (0-30 = Debut..UG)
.popularity                         Int32     8            — favorite rank (odds position)
.popularity_mark_rank_array         Int32[3]  e.g. [9,9,3] — pre-race favorite marks

.proper_distance_short              Int32     6            — aptitude 1..8 (1=G, 8=S)
.proper_distance_mile               Int32     7
.proper_distance_middle             Int32     6
.proper_distance_long               Int32     5
.proper_running_style_nige          Int32     7
.proper_running_style_senko         Int32     3
.proper_running_style_sashi         Int32     1
.proper_running_style_oikomi        Int32     1
.proper_ground_turf                 Int32     8
.proper_ground_dirt                 Int32     4

.motivation                         Int32     5
.mob_id                             Int32     0
.win_saddle_id_array                Int32[]   e.g. [10]    — winning saddles inherited
.race_result_array                  RaceHorseDataRaceResult[]  len=0 in this capture — see below
.team_id                            Int32     10
.team_member_id                     Int32     1
.item_id_array                      Int32[]   len=0
.motivation_change_flag             Int32     0
.frame_order_change_flag            Int32     0
.team_rank                          Int32     0
.single_mode_win_count              Int32     11
.fan_count                          Int32     282575
```

**11 real players captured in the sample**: Trubber, Lennox, Cano,
BeUwUlf12, and 7 others (full list in
[`scout_room_match_phase3.log`](room-match-scout/scout_room_match_phase3.log)).

### `_raceScenario` — decoded ✅ (2026-08-04)

```
._raceScenario                      ObscuredString
   .currentCryptoKey                string    "ptgvmuyz"    — XOR key
   .hiddenValue                     Byte[]    len=31264     — ciphertext
   .inited                          bool      true
```

**Fully decoded.** The chain, each step verified against a live
capture:

```
hiddenValue (byte[], UTF-16LE code units)
  -> XOR each 16-bit char with currentCryptoKey[i % len]
  -> ASCII base64 text ("H4sIAAAA…" = gzip magic)
  -> base64 decode        (31264 obscured bytes -> 11723)
  -> gunzip               (-> 19785 bytes)
  -> packed little-endian binary — NOT msgpack
```

The key is ASCII, so the XOR only ever touches the low byte of each
UTF-16 unit — which is why the raw bytes read `38 00 40 00 …`. That
pattern is the tell that this is an obscured *string*, not a byte
blob.

Reference implementation, with a self-test against the checked-in
fixture: [`room-match-scout/scenario_decode.py`](room-match-scout/scenario_decode.py).

#### Per-horse result table — where the outcome actually lives

Inside the plaintext, 11 records of **31 bytes** (one per runner,
indexed by gate = `frame_order - 1`, same order as
`_raceHorseDataArray`). In the sample capture the table starts at
offset 14320, but the decoder locates it by structure rather than
hardcoding — the offset moves with the number of runners.

```
 0   u32    finishing position, 0-based
 4   f32    finish time, seconds            e.g. 90.533
 8   f32    gap to the horse ahead, seconds (0.0 for the winner)
12   f32    unidentified (small positive float)
16   u8     permutation of 0..N-1 — running order at a checkpoint
17   u8     permutation of 0..N-1 — ditto, a different checkpoint
22   u8     running_style (1 nige / 2 senko / 3 sashi / 4 oikomi)
27   f32    unidentified (seconds, ~0.8x the finish time)
```

**Cross-validated three independent ways** on the sample capture:

1. The decoded finishing order is exactly the placings Uma Ladder had
   already recorded for that race (Aisha AlSadhazi → StarlitFire →
   steelbeuwulf12 → Yuuta → …).
2. Finish times increase monotonically with finishing position, and
   each runner's `gap` equals the time difference to the runner
   directly ahead — to the millisecond.
3. Byte 22 of every record equals that runner's
   `RaceHorseData.running_style`, read independently out of live
   memory.

This is the finding that unblocks the whole project: **placements,
finish times and margins are all available**, so a capture is a
complete result, not just a build sheet.

#### What else is in the blob (19,785 bytes total)

The result table is only 341 of those bytes. Section map, with an
honest confidence level on each:

| range | size | contents | status |
|---|---|---|---|
| 0 – 64 | 64 B | header — version, horse count (11), race params | **read** |
| 64 – 14320 | ~14 KB | per-runner race telemetry (the "replay") | **not decoded** |
| 14320 – 14661 | 341 B | per-horse result table | **decoded + validated** |
| 14673 – 19785 | ~5 KB | per-runner skill records (154) | **decoded + validated** |

**Skill records** — 114 records of (mostly) 32 bytes:

```
+4   i32   runner index (0-based, gate order)
+8   i32   skill_id       — resolves against the GameTora catalog
+12  i32   -1 = never fired, otherwise a position/phase value
+20  i32   flag, always a power of two
```

Verified: every skill_id is a real catalog entry (`200011` →
"Right-Handed ◎", `200431` → "Concentration", `201662` → "Head-On"),
and **all 154 records name a skill that runner actually had
equipped** — 100%, checked against `RaceHorseData.skill_array` read
independently from memory. 122 of the records are activations, 32
never fired.

(An earlier pass reported 103/114. That was an artefact of parsing
equipped skills out of the truncated phase-3 *log* — runners carry
15–24 skills, not the 12 the log showed. Measured against the real
lists from a full capture, the match is exact. Worth remembering as a
method note: validate against captured data, never against a log
that was written for human reading.)

Open on this section: record size is not uniformly 32 bytes (skill_id
offsets land on four different residues mod 32), so a parser must walk
the section rather than stride it. And field `+12` is *not* simply
"metres into the race" — across this capture it takes only 8 distinct
values on a coarse grid, which does not look like an exact track
position. Do not ship an interpretation of `+12` without a second
capture to test it against.

**Telemetry section** — undecoded, and now known *not* to be a flat
array. A second capture settled it:

- A 12-byte fixed stride looked convincing on the 11-runner race —
  distances stepping 100/150/200 m at 20–21 m/s, exactly right for
  this game. It was coincidence. `14256 = 11 × 1296`, and 1296 happens
  to divide by 4, 6, 8, 12 and 16, so *every* width "fit".
- On the 18-runner race (34296 B) **no width divides evenly at all**,
  which a per-runner × per-frame × fixed-width block would have to.
- An anchored search over both races — every (offset, stride, width)
  that could produce a monotonically increasing series of 40+ samples,
  which any position or time track must be — returned **zero hits**.

So the section is delta-encoded, bit-packed, or otherwise compressed
rather than a plain array of samples. That also explains the earlier
symptoms (distances "resetting" 266 times, implied speeds of
650 m/s): those were misaligned reads of packed data, not corrupt
runners.

Cracking it needs bit-level analysis, not stride hunting. Useful next
probes: byte-value histogram (near-uniform ⇒ bit-packed), and a
capture of the *same* race replayed twice (byte-identical ⇒
deterministic, so the encoding can be diffed against a known-varying
input).

None of this blocks the extractor: the result table plus the skill
records already exceed what OCR could ever see. The telemetry is a
later prize (pace charts, sectional times), not a dependency.

### The telemetry does not need cracking — the game parses it for us ✅

After three byte-level hypotheses were falsified, the better move was
to stop reverse-engineering the encoding and find the code that reads
it. `Gallop.IRaceSimulateImporter` exposes
`RaceSimulateData get_ImportedSimData()` — and while a replay is
loaded, **a fully parsed `Gallop.RaceSimulateData` is live in memory**:

```
Gallop.RaceSimulateData
  .Header                  RaceSimulateDataHeader
  ._frameDataList          List<RaceSimulateFrameData>     — the replay
  ._horseResultDataArray   RaceSimulateHorseResultData[]   — outcomes
  ._simEvDataList          List<RaceSimulateEventData>     — typed events
  ._horseNum / _lastFrameIdx / _lastCalcFrameTime

Gallop.RaceSimulateFrameData
  .Time             float
  .HorseDataArray   RaceSimulateHorseFrameData[]   — per runner, per frame

Gallop.RaceSimulateEventData
  .frameTime   float
  .type        SimulateEventType
  .param       int[]            — variable length; see "The event stream"

Gallop.RaceSimulateHorseResultData
  .FinishOrder .FinishTime .FinishTimeRaw .FinishDiffTime
  .StartDelayTime .GutsOrder .WizOrder .LastSpurtStartDistance
  .RunningStyle .Defeat
```

On the 3600 m capture that object held **230 frames × 9 runners** and
**70 typed events** — i.e. the entire ~25 KB telemetry section, already
decoded, plus fields the binary decode never surfaced:
`LastSpurtStartDistance` (where each runner kicked), `StartDelayTime`
(gate reaction), `FinishTimeRaw`, `GutsOrder`, `WizOrder`, `Defeat`.

The per-frame leaf record is the whole replay:

```
Gallop.RaceSimulateHorseFrameData      (per runner, per frame)
  .Distance              float   0.0 -> 3787.9 m  monotonic over the race
  .LanePosition          float   lateral position on the track
  .Speed                 float   3.0 -> 23.45 m/s
  .Hp                    float   4619 -> 556      stamina depletion curve
  .TemptationMode        sbyte   kakari / pulling state
  .BlockFrontHorseIndex  sbyte   runner blocking this one (-1 = clear)
```

So **the replay is fully recoverable** — position, lane, speed,
stamina, temptation and traffic for every runner at every frame. We
simply never needed to decode the bytes: the game does it for us.
Sample dump checked in at
`room-match-scout/captures/85520900_800072.frames.json`.

Frame spacing is uneven — 1/15 s at the start (0.067, 0.133,
0.200 ...) but ~1.07 s mid-race. This is genuine variable-rate
keyframing, not an artefact: it reproduces across captures and across
re-reads of the same replay. Anything that plays the data back has to
interpolate between samples rather than step them, or runners teleport
~20 m per tick mid-race (see PR-X9).

**The byte decoder was independently confirmed by this.** Run against
the same race, the two agree on every finishing position, every finish
time to within 0.0004 s, and every running style — 9/9. So the
hand-rolled decoder is correct; it is simply a subset of what the
game's own parse gives.

#### Which source to use when

| | saved-result byte decode | live `RaceSimulateData` |
|---|---|---|
| available | any time after the race, no replay needed | only while a replay is loaded |
| gives | **everything** — frames, results, typed events (see "blob layout is public" below; server parses it via `uma_ladder/services/race_blob.py`) | the same data, plus **course geometry** from `RaceManager` |
| risk | none beyond the read itself | needs the user to open a replay |

~~The extractor should do both...~~ **Superseded 2026-08-06:** the
blob turned out to carry the complete replay, and the site now parses
it server-side for any capture (retroactively included). The live
read's remaining unique value is course geometry and cross-validation.

### The blob layout is public — hakuraku already ships it ✅ (2026-08-06)

[hakuraku.moe](https://hakuraku.moe) (MIT, [github.com/ayaliz/hakuraku](https://github.com/ayaliz/hakuraku),
actively developed) is a community race-analysis site fed by
packet-captured race data. Its `src/data/RaceDataParser.ts` documents
the **complete scenario-blob layout**, which we verified byte-for-byte
against our own InyanyaCup #1 blob AND cross-checked against the live
`RaceSimulateData` capture of the same race — every value agrees:

```
header:       int32 maxLength, int32 version              (4 + maxLength bytes)
race struct:  f32 distanceDiffMax, i32 horseNum,
              i32 horseFrameSize (12), i32 horseResultSize (31)
padding:      i32 size + size bytes            (×3, between each section)
frame block:  i32 frameCount, i32 frameSize, then frameCount frames:
                f32 time, then per horse (12 bytes):
                f32 distance, u16 lane (×1/10000), u16 speed (×1/100),
                u16 hp, i8 temptationMode, i8 blockFrontHorseIndex
result block: horseNum × 31 bytes:
                i32 finishOrder, f32 finishTime, f32 finishDiffTime,
                f32 startDelayTime, u8 gutsOrder, u8 wizOrder,
                f32 lastSpurtStartDistance, u8 runningStyle,
                i32 defeat, f32 finishTimeRaw
event block:  i32 eventCount, then per event:
                i16 eventSize, f32 frameTime, i8 type, i8 paramCount,
                paramCount × i32
```

Consequences:

- **The saved result carries the FULL replay.** Frames, results and
  events all live in the blob — the "replay must be open" constraint
  on telemetry was never real. Parsing frames from the blob gives
  every capture a replay. (Course geometry is the one thing still
  live-only — it sits on `RaceManager`, not in the blob.)
- **Why our earlier byte-level sweeps failed**: we swept for float32
  speed arrays, but lane/speed/hp are quantized u16; and we divided
  the telemetry span evenly without the 4-byte frame time, so no
  stride matched. Both hypotheses were falsified for the right data
  in the wrong encoding.
- Our hand-cracked 31-byte result record matches their layout
  offset-for-offset, and their event record is exactly the shape we
  decoded from live memory.
- Their JP fallback parser shows the JP client uses 39-byte core
  results and more event types (`STAMINA_LIMIT_BREAK_BUFF`,
  `COMPETE_BEFORE_SPURT`, `STAMINA_KEEP`, `SECURE_LEAD`, Zenkai
  Spurt) — Global will likely gain these; unknown event types must
  stay non-fatal.
- Their derived diagnostics are a roadmap for our race page: start
  delay classification, last-spurt delay ("potential spurt issue"),
  HP death point ("Died −10m"), duel time, downhill-mode procs,
  pace up/down time, wit-lottery analysis, position-keep heuristics.
  All computable from data we already store.

### The event stream — decoded ✅ (2026-08-05)

`_simEvDataList` is what the frame data can't express: *why* the race
changed shape. Scouted live from an 11-runner replay (`scout_events.py`,
raw dump at `room-match-scout/events.json`).

`Gallop.SimulateEventType` has seven members, of which four occur:

| member | seen | meaning |
|---|---|---|
| `Skill` | 155 | a skill slot resolving — fired *or not* |
| `CompeteFight` | 5 | runners fighting for position |
| `ReleaseConservePower` | 5 | held-back stamina released |
| `CompeteTop` | 3 | contesting the lead |
| `Score`, `ChallengeMatchPoint`, `NOUSE_2` | 0 | other modes |

`param` is variable-length: 1 int for the non-skill events (the runner
index), 6 for `Skill`:

```
param[0]  caster runner index      0..N-1
param[1]  skill id                 == UmaSkill.gametora_id
param[2]  duration, 1/10000 s      -1 => equipped but never fired
param[3]  always 0 in every sample
param[4]  affected-runner bitmask  1 << i per runner affected; 0 if never fired
param[5]  always 0 in every sample
```

Two decodes worth calling out:

**`param[4]` is a bitmask, and it identifies debuffs.** Most values are
a single bit equal to `1 << param[0]` — a self-buff. Multi-bit values
are skills thrown at other runners. This is self-validating: in the
sample race, *Hesitant Front Runners* (cast by runner 2) carried mask
`177` = runners 0, 4, 5, 7 — exactly the four runners who had fired
*Top Runner* and triggered `CompeteTop`. The engine's own idea of "who
is front-running" agreed with ours.

**`param[2]` is a duration in 1/10000 s, already course-scaled** —
confirmed across three distances ✅ (2026-08-06). So
`duration_seconds = param[2] / 10000`, with no further adjustment.

This could only be settled by comparing distances: on one course the
`/10000` and the length scaling are mathematically indistinguishable.
Across the 1600 / 2200 / 3600 m corpus, the same skill id reads:

| | 1600 m | 2200 m | 3600 m | implied base |
|---|---|---|---|---|
| skill 201321 | 48000 | 66000 | 107999 | 3.0 s |
| skill 200462 | 28799 | 39600 | 64799 | 1.8 s |
| skill 200331 | 38399 | 52799 | 86399 | 2.4 s |

i.e. `param[2] = base × 10000 × distance/1000`. Of 28 skills appearing
at more than one distance, 20 agree to within float-truncation noise,
6 are zero-duration (instant effects), and 2 differ because the same
skill id can carry different base durations per instance (level). Every
implied base lands on a canonical value — 0.9, 1.2, 1.8, 2.4, 3.0 s —
which no incorrect unit would produce. Locked in by
`test_skill_duration_is_course_scaled_tenthousandths`.

`frameTime` is on the simulation clock, so it needs the same rescale as
the frame timestamps (below).

### Course geometry — decoded ✅ (2026-08-05)

The client holds the shape of the loaded course on `Gallop.RaceManager`
(`scout_course.py`, dump at `room-match-scout/course.json`):

```
Gallop.RaceManager
  ._straightList   List<CourseStraight>   StartDistance, EndDistance, Range, frontType
  ._cornerList     List<CourseCorner>     StartDistance, EndDistance, cornerNumber, IsFinalCorner
  ._slopeList      List<CourseSlope>      StartDistance, EndDistance, SlopeType (Up/Down)
```

Kyoto 1600 m reads as: straight 200–720 (uphill 520–695), corner 3
720–995, **final corner 995–1272**, home straight 1272–1600 (downhill
820–970). The lists are authoritative (`_size` matches), and the
opening 0–200 m is deliberately unlisted — it's the start chute.

This matters because it means **no GameTora course import is needed**:
each capture carries its own geometry, correct for whatever venue ran.
It lives only while a race or replay is loaded, so the extractor reads
it in the same pass as the sim data — but independently, since either
can be present without the other.

### Two clocks: simulation time vs quoted race time ⚠️

Frame and event timestamps are **not** the times the game reports as
finishing times. On the sample race, frames run 0 → 75.86 s while the
winner's `FinishTime` is 90.533 s.

The relationship is a constant. Interpolating each runner's frame time
at the finish line and dividing gives 1.21330–1.21371 across all
eleven — a spread of 0.0004, i.e. interpolation error alone.

Do not hard-code it. `_time_scale()` in `services/race_captures.py`
measures it per capture from `FinishTime` ÷ interpolated crossing time
and takes the median, so a race with a different factor (or a capture
with one bad trace) still lines up. Without this the replay clock ends
at 76 s for a race the results table calls 90 s.

Related: the telemetry runs *past* the line — runners pull up — so the
last sampled distance (1631 m here) is not the course length. Use the
geometry's maximum end distance for anything that bins or scales by
distance, or sectionals credit metres that were never raced.

**`LastSpurtStartDistance` corroborates the 2/3 phase edge.** Every
runner's game-supplied spurt point on the 1600 m course landed at
~1069 m = 0.668 of the course, against the community simulators'
2/3 convention. The 1/6 opening edge remains unverified convention.

### Cross-race validation (2 captures, 2026-08-04)

| | race A | race B |
|---|---|---|
| instance | 800095 | 800074 |
| runners | 11 | 18 |
| distance | 1600 m (Kyoto) | 2200 m (Nakayama) |
| weather / ground | Sunny / Firm | Rainy / Soft |
| scenario | 31,264 B → 19,785 B | 67,248 B → 38,850 B |

The decoder was written against race A and run unchanged on race B:

- finishing positions form a complete 1..N in both — **11/11, 18/18**
- finish times monotonic with position in both
- each runner's `gap` reconstructs the time difference to the runner
  ahead, to the millisecond, in both
- `running_style` from the scenario matches the value read
  independently out of memory — **11/11 and 18/18**
- skill records map to the right runner's equipped list — 154/154 on
  race A, 137/139 on race B

`find_result_table` locates the table structurally, so the changed
runner count needed no code change. The two stray skill records on
race B (~1.4%) are unexplained — most likely the non-uniform record
size, possibly rival-applied debuffs. Worth a third capture before
trusting that section at 100%.

**Robustness lesson for the extractor:** the 18-runner race crashed
the first capture attempt with an access violation — some runners
carry fields whose getters dereference bad memory. Every field read,
every array element, and every skill slot needs its own try/catch, and
a bad runner must not lose the other 17. The shipped
`capture_room_match.py` does this.

### `.race_result_array` is always empty — resolved ✅

Re-scouted 2026-08-04: still `len=0` for all 11 runners, and dumping
the complete field list of `RaceHorseData` confirms the class has **no
outcome field at all** — no finish order, no result rank, no finish
time. The fields that look like results are not:

- `popularity` — pre-race favorite rank (odds position), not the finish
- `final_grade` — the uma's grade (0–30 = Debut..UG), not the placing

So the encrypted scenario is the *only* source of the outcome, and it
decodes (above). No need to open the Detail dialog.

### Capture window is much wider than assumed ✅

The doc originally said "run while on the Room Match result screen".
Re-scouting on 2026-08-04 with the game sitting elsewhere:

- `_savedRaceResultInfo` was **still fully populated** — same
  `saved_room_id`, all 11 runners, scenario intact.
- `_raceResultInfo` was null (access violation on read) — that one *is*
  screen-scoped.
- `_currentRoomUserList` had emptied to size 0 (the room was left), so
  the `viewer_id -> trainer_name` mapping must be taken from
  `_raceHorseDataArray`, which carries `trainer_name` per runner
  anyway.

Practically: the extractor does not need the player to be parked on the
result screen. It can be run any time after the race until the client
replaces the saved result. That removes the most awkward part of the
UX (the IT extractor has exactly this constraint and it is the #1
support question on the sibling project).

### `_currentRoomData` (room settings, live)

Reachable and useful as a secondary source; note the strings here are
`ObscuredString` (same de-obscuring as the scenario) whereas
`_raceResult` carries them plain:

```
._savedRoomId                 34408987     — matches _raceResult
._registerId / StartUnixTime  1785108645   — unix start time
._currentEntryNum             11
._roomName / ._message / ._startTime       — ObscuredString
._hostUser                    UserData
```

### Scout crash (non-blocking, phase 3 only)

Phase 3 crashed with an access violation recursing into
`ObscuredString.hiddenValue`. Phases 4–5 avoid it by reading the byte
array in one shot (`.elements.handle.readByteArray(len)`) instead of
walking it element-wise. Nothing was lost.

## Cross-repo plan

Two repos are involved. Uma Ladder is the consumer; the
[uma-it-optimizer](https://github.com/Acrith/uma-it-optimizer) sibling
holds all the IL2CPP tooling to actually do the memory read.

### Extractor side (uma-it-optimizer)

Scout scripts and evidence already live in that repo at
`tools/memory_extractor/scout_room_match*.{py,log}`. Suggested
build order:

1. ~~**Verify `_raceScenario` decodes**~~ — **done 2026-08-04.** It is
   UTF-16 XOR -> base64 -> gzip -> packed binary (not msgpack), and it
   contains the finishing order, finish times and margins. Reference
   decoder + fixture:
   [`room-match-scout/scenario_decode.py`](room-match-scout/scenario_decode.py).
   Port it into the extractor as-is.
2. **Build the `uma-race-extract` binary** — mirrors the existing
   `dump_it_run.py`: attaches Frida, walks
   `WorkRoomMatchData._savedRaceResultInfo`, serializes to JSON,
   uploads to a new Uma Ladder API endpoint. Reuse
   `_find_pid`, `_find_wine_hosted_process_pid`, retry logic, auth
   token flow from the existing extractor. 1–2 days.
3. **Decide extractor packaging** — single `.exe` that auto-detects
   which screen the game is on (Training Log popup → IT dump;
   Room Match result → race dump) is cleaner than two separate
   binaries.

### Uma Ladder side (this repo)

Per PROJECT_INTENTIONS working rules: no branches / no commits from
Claude, small reviewable changes, always list changed files + test
plan + risks + next step per implementation step. Suggested
implementation order — each is one small change:

1. **Migration + model** — `race_capture` table (or extend the
   existing result submission table) with a `source` discriminator
   (`ocr` | `memory_scan`), a `raw_json` column for the extractor
   payload, and a `verified_by_user_id` column for the human-confirm
   step.
   - *Changed*: `uma_ladder/models/…`, `migrations/…`
   - *Test*: create + read one via a shell fixture; verify migration
     up/down.
   - *Risk*: schema churn once we start ingesting real payloads —
     start with a permissive JSONB column.
2. **Ingest endpoint** — `POST /api/race-captures` accepting the
   extractor JSON (schema in this doc), authed by user API token
   (same shape as `uma-it-web` already ships). Returns a URL to the
   pending-confirmation review page.
   - *Changed*: new blueprint under `uma_ladder/api/…`
   - *Test*: submit the phase-3-scout JSON via curl; assert 201 +
     row exists.
   - *Risk*: token flow doesn't exist yet — this may depend on user
     API-token infrastructure landing first.
3. **Review + confirm UI** — HTMX page listing pending captures for
   the current user; each row expands into the 11-runner table with
   editable placements; "Save to ladder" hits the same code path
   OCR submissions do today.
   - *Changed*: `uma_ladder/results/…`, Jinja templates.
   - *Test*: manual walkthrough with a seeded pending capture.
   - *Risk*: OCR path may have UI assumptions this needs to match.
4. **Draft PvP integration** — hook the confirmed capture into the
   Draft PvP Elo/ladder update. May be a no-op if the existing
   confirm path already writes to the ladder.
5. **Privacy defaults** — a memory capture implicates 10 other
   players' names, uma builds, and stats. Public-share views should
   default to anonymizing everyone except the submitter (opt-in
   reveal per player, or org-level "share full detail" toggle for
   organizer-run official races where public detail is expected).

### Consumer-of-data question that only Uma Ladder can answer

The extractor JSON payload is generous — 11 runners with 40+ fields
each plus room metadata plus (eventually) a decoded replay. Uma
Ladder should decide, before the extractor endpoint ships, **what
subset the ladder actually stores** vs. what stays in the raw blob:

- Placements + times + player identity → ladder core
- Per-runner stats + skills + factors → useful for meta reports but
  large; store denormalized for aggregation?
- Replay (position per frame) → hundreds of KB per race; probably
  raw blob only, expose on request

## Reference

- **Scout logs (evidence):**
  [`room-match-scout/scout_room_match.log`](room-match-scout/scout_room_match.log) — phase 1, class enumeration, 1490 candidates
  [`room-match-scout/scout_room_match_phase2.log`](room-match-scout/scout_room_match_phase2.log) — phase 2, targeted heap scan, confirmed `WorkRoomMatchData` live
  [`room-match-scout/scout_room_match_phase3.log`](room-match-scout/scout_room_match_phase3.log) — phase 3, full field dump for all 11 runners
- **Scout scripts** (also live in
  `uma-it-optimizer/tools/memory_extractor/` — that's where they run
  from):
  [`room-match-scout/scout_room_match.py`](room-match-scout/scout_room_match.py)
  [`room-match-scout/scout_room_match_phase2.py`](room-match-scout/scout_room_match_phase2.py)
  [`room-match-scout/scout_room_match_phase3.py`](room-match-scout/scout_room_match_phase3.py)
  [`room-match-scout/scout_live_state.py`](room-match-scout/scout_live_state.py) — phase 4a, what is live right now
  [`room-match-scout/scout_phase4_outcome.py`](room-match-scout/scout_phase4_outcome.py) — phase 4, proves the outcome is not in RaceHorseData
  [`room-match-scout/scout_phase5_scenario.py`](room-match-scout/scout_phase5_scenario.py) — phase 5, lifts the scenario bytes out
  [`room-match-scout/scenario_decode.py`](room-match-scout/scenario_decode.py) — **the decoder**, with a self-test
- **Fixture:** `room-match-scout/fixtures/scenario_raw_inyanyacup1.bin`
  (+ key) — the real 31 KB obscured scenario, so the decoder and the
  future extractor can be unit-tested without the game running.
- **Sibling projects:**
  [uma-it-optimizer](https://github.com/Acrith/uma-it-optimizer) —
  Frida scaffolding, `.exe` extractor, Hachimi plugin, all IL2CPP
  tooling. Fork the room-match extractor from `dump_it_run.py`.
  [training.umaladder.moe](https://training.umaladder.moe) — the IT
  training dashboard; different product, but its extractor upload
  auth pattern is what to mirror for the Room Match endpoint.
- **Capture context:** InyanyaCup ?1, 11 participants,
  `race_instance_id` 800095, `start_time` 2026-07-26 23:30:45,
  `random_seed` 1973968693. Captured on 2026-08-04 from a live
  Windows Umamusume PID via Frida.
