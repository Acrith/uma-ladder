"""Reference decoder for the Room Match `_raceScenario` blob.

This is the piece that makes an OCR-free result pipeline possible: the
finishing order, finish times and margins are *only* in here — phase 4
proved `RaceHorseData.race_result_array` is always empty and the class
has no outcome field at all.

Decode chain (each step verified against a live capture on 2026-08-04):

    ObscuredString.hiddenValue   byte[] , UTF-16LE code units
      -> XOR each 16-bit char with currentCryptoKey[i % len]
      -> ASCII base64 text  ("H4sIAAAA..." — gzip magic)
      -> base64 decode
      -> gunzip
      -> packed little-endian binary  (NOT msgpack)

Inside the plaintext, the per-horse result table is 11 records of 31
bytes at offset 14320 (see `find_result_table` for how to locate it
without hardcoding — the offset moves with field size).

Per-record layout, offsets relative to the record start:

    0   u32    finishing position, 0-based
    4   f32    finish time, seconds
    8   f32    gap to the horse immediately ahead, seconds (0 for winner)
    12  f32    (unidentified — small positive float)
    16  u8     (permutation of 0..N-1 — running order at some checkpoint)
    17  u8     (permutation of 0..N-1 — ditto, different checkpoint)
    22  u8     running_style  (1 nige / 2 senko / 3 sashi / 4 oikomi)
    27  f32    (unidentified — seconds, ~0.8x finish time)

Records are indexed by gate (frame_order - 1), matching the order of
`SavedRaceResultData._raceHorseDataArray`.

Cross-validation on the sample capture (InyanyaCup #1, 11 runners):
  * decoded finishing order == the placings Uma Ladder already had
    recorded for that race,
  * finish times are monotonically increasing with finishing position,
  * byte 22 of every record == `RaceHorseData.running_style` read
    independently out of live memory.

Run this file directly to re-verify against the checked-in fixture.
"""

from __future__ import annotations

import base64
import gzip
import struct
from dataclasses import dataclass
from pathlib import Path

RECORD_SIZE = 31
_OFF_FINISH = 0
_OFF_TIME = 4
_OFF_GAP = 8
_OFF_STYLE = 22


@dataclass(frozen=True)
class HorseResult:
    """One runner's outcome. `gate` is 1-based (frame_order)."""

    gate: int
    finish_position: int  # 1-based, ready for the ladder
    finish_time_seconds: float
    gap_to_ahead_seconds: float
    running_style: int


class ScenarioDecodeError(Exception):
    pass


def deobscure(hidden_value: bytes, crypto_key: str) -> str:
    """Undo CodeStage.AntiCheat ObscuredString.

    `hidden_value` is the raw byte[] straight out of memory: UTF-16LE
    code units, each XOR'd with the cycling key. The key is ASCII so
    only the low byte is ever touched, which is why the raw bytes look
    like `38 00 40 00 ...`.
    """
    if not crypto_key:
        raise ScenarioDecodeError("empty crypto key")
    if len(hidden_value) % 2:
        raise ScenarioDecodeError("hiddenValue is not a whole number of UTF-16 units")
    key = [ord(c) for c in crypto_key]
    out = []
    for i in range(0, len(hidden_value), 2):
        unit = hidden_value[i] | (hidden_value[i + 1] << 8)
        out.append(chr(unit ^ key[(i // 2) % len(key)]))
    return "".join(out)


def inflate_scenario(hidden_value: bytes, crypto_key: str) -> bytes:
    """Full chain: obscured bytes -> plaintext binary scenario."""
    text = deobscure(hidden_value, crypto_key)
    try:
        packed = base64.b64decode(text, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ScenarioDecodeError(f"not valid base64 after de-obscuring: {exc}") from exc
    if packed[:2] != b"\x1f\x8b":
        raise ScenarioDecodeError(f"expected gzip magic, got {packed[:4].hex()}")
    return gzip.decompress(packed)


def find_result_table(plain: bytes, horse_count: int) -> int:
    """Locate the per-horse result table without hardcoding an offset.

    The table is the only place where the first field of `horse_count`
    consecutive 31-byte records forms a permutation of 0..horse_count-1
    *and* the accompanying float32 finish times increase with it. That
    pair of constraints is specific enough that a scan finds exactly
    one candidate on real captures.
    """
    want = set(range(horse_count))
    limit = len(plain) - horse_count * RECORD_SIZE
    for base in range(0, max(0, limit)):
        finishes = []
        for i in range(horse_count):
            (v,) = struct.unpack_from("<I", plain, base + i * RECORD_SIZE)
            if v >= horse_count:
                break
            finishes.append(v)
        else:
            if set(finishes) != want:
                continue
            times = [
                struct.unpack_from("<f", plain, base + i * RECORD_SIZE + _OFF_TIME)[0]
                for i in range(horse_count)
            ]
            if any(not (0.0 < t < 1000.0) for t in times):
                continue
            ordered = [t for _, t in sorted(zip(finishes, times, strict=True))]
            if all(a <= b for a, b in zip(ordered, ordered[1:], strict=False)):
                return base
    raise ScenarioDecodeError("could not locate the per-horse result table")


def parse_results(plain: bytes, horse_count: int) -> list[HorseResult]:
    """Per-runner outcome, in gate order."""
    base = find_result_table(plain, horse_count)
    out: list[HorseResult] = []
    for i in range(horse_count):
        off = base + i * RECORD_SIZE
        (finish,) = struct.unpack_from("<I", plain, off + _OFF_FINISH)
        (time_s,) = struct.unpack_from("<f", plain, off + _OFF_TIME)
        (gap_s,) = struct.unpack_from("<f", plain, off + _OFF_GAP)
        style = plain[off + _OFF_STYLE]
        out.append(
            HorseResult(
                gate=i + 1,
                finish_position=finish + 1,
                finish_time_seconds=round(time_s, 3),
                gap_to_ahead_seconds=round(gap_s, 4),
                running_style=style,
            )
        )
    return out


def decode(hidden_value: bytes, crypto_key: str, horse_count: int) -> list[HorseResult]:
    """One-shot: obscured memory bytes -> per-runner outcomes."""
    return parse_results(inflate_scenario(hidden_value, crypto_key), horse_count)


if __name__ == "__main__":  # pragma: no cover - manual verification
    here = Path(__file__).parent / "fixtures"
    raw = (here / "scenario_raw_inyanyacup1.bin").read_bytes()
    key = (here / "scenario_key_inyanyacup1.txt").read_text().strip()

    # Gate order of the sample capture, for a readable printout.
    names = [
        "Trubber", "Lennox", "Cano", "BeUwUlf12", "StarlitFire",
        "Aisha AlSadhazi", "Riso", "Kezuke", "Acrith", "Yuuta", "Ryada",
    ]
    results = decode(raw, key, horse_count=11)
    print(f"decoded {len(results)} runners from {len(raw)} obscured bytes\n")
    for r in sorted(results, key=lambda x: x.finish_position):
        print(
            f"  {r.finish_position:2d}. {names[r.gate - 1]:<16} "
            f"{r.finish_time_seconds:7.3f}s  +{r.gap_to_ahead_seconds:.3f}  "
            f"gate {r.gate:2d}  style {r.running_style}"
        )

    # The placings Uma Ladder independently recorded for this race.
    expected = [
        "Aisha AlSadhazi", "StarlitFire", "BeUwUlf12", "Yuuta", "Lennox",
        "Acrith", "Kezuke", "Cano", "Trubber", "Ryada", "Riso",
    ]
    got = [names[r.gate - 1] for r in sorted(results, key=lambda x: x.finish_position)]
    print("\nmatches the ladder's recorded order:", got == expected)
