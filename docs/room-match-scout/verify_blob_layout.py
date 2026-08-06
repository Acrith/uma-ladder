"""Verify the hakuraku scenario-blob layout against our own capture.

Run from the repo root:  .venv/bin/python docs/room-match-scout/verify_blob_layout.py

Parses the InyanyaCup #1 blob with the layout from
vendor/hakuraku_RaceDataParser.ts and prints the fields that can be
cross-checked against the live RaceSimulateData capture of the same
race (captures/34408987_800095.withevents.json). Verified 2026-08-06:
every value agrees — 105 frames, last frame 75.857 s @ 1608.49 m,
runner 0 finishing 8th in 91.612 s with spurt at 1069.2 m, 168 events,
first event (0, 200011, -1, 0, 0, 0).

The point: frames, results AND events all live in the saved-result
blob. Telemetry is quantized (lane u16 x1/10000, speed u16 x1/100,
hp u16), which is why earlier float32 sweeps found nothing.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / ".." / ".." / "tools" / "race_extractor"))
from scenario_decode import inflate_scenario  # noqa: E402


def main() -> None:
    hidden = (BASE_DIR / "fixtures" / "scenario_raw_inyanyacup1.bin").read_bytes()
    key = (BASE_DIR / "fixtures" / "scenario_key_inyanyacup1.txt").read_text().strip()
    raw = inflate_scenario(hidden, key)
    print("inflated size", len(raw))

    # header: i32 maxLength, then maxLength bytes (version inside)
    maxlen, version = struct.unpack_from("<ii", raw, 0)
    print("header maxLength", maxlen, "version", version)
    off = 4 + maxlen

    # race struct
    ddm, horse_num, frame_rec, result_rec = struct.unpack_from("<fiii", raw, off)
    print(f"distanceDiffMax {ddm:.2f} horseNum {horse_num} "
          f"horseFrameSize {frame_rec} horseResultSize {result_rec}")
    off += 16

    pad1, = struct.unpack_from("<i", raw, off)
    off += 4 + pad1

    frame_count, frame_size = struct.unpack_from("<ii", raw, off)
    off += 8
    print("frameCount", frame_count, "frameSize", frame_size,
          "(expect", 4 + horse_num * frame_rec, ")")

    # sample three frames, runner 0
    for i in (0, frame_count // 2, frame_count - 1):
        o = off + i * frame_size
        t, = struct.unpack_from("<f", raw, o)
        d, lane, spd, hp, tempt, blk = struct.unpack_from("<fHHHbb", raw, o + 4)
        print(f"  frame {i:3d}: t={t:7.3f} dist={d:8.2f} "
              f"lane={lane / 10000:.3f} speed={spd / 100:.2f} hp={hp} "
              f"tempt={tempt} blk={blk}")
    off += frame_count * frame_size

    pad2, = struct.unpack_from("<i", raw, off)
    off += 4 + pad2

    # 31-byte result records — offsets match our hand-cracked decode
    fin, ft, fdt, sdt, guts, wiz, spurt = struct.unpack_from("<ifffBBf", raw, off)
    print("first horseResult: finishOrder", fin, "finishTime", round(ft, 3),
          "startDelay", round(sdt, 4), "spurt", round(spurt, 1))
    off += horse_num * result_rec

    pad3, = struct.unpack_from("<i", raw, off)
    off += 4 + pad3

    event_count, = struct.unpack_from("<i", raw, off)
    off += 4
    print("eventCount", event_count)
    esize, = struct.unpack_from("<h", raw, off)
    t, etype, pcount = struct.unpack_from("<fbb", raw, off + 2)
    params = struct.unpack_from(f"<{pcount}i", raw, off + 8)
    print("first event: size", esize, "t", round(t, 3),
          "type", etype, "params", params)


if __name__ == "__main__":
    main()
