"""Parse the full race replay out of a capture's scenario blob.

Every capture carries the game's saved-result scenario — an obscured,
gzipped binary. The extractor decodes the finishing order from it, but
the blob holds far more: the complete frame-by-frame replay (position,
lane, speed, stamina per runner), the per-runner result records, and
the typed event stream. This module parses all of it server-side, so a
capture taken from the plain result screen — no replay open in the
game — still gets a full replay on the race page, and captures
uploaded before this existed gain one retroactively.

The layout was cross-verified byte-for-byte against a live
``Gallop.RaceSimulateData`` read of the same race (2026-08-06), and
matches the open-source hakuraku parser
(``docs/room-match-scout/vendor/hakuraku_RaceDataParser.ts``, MIT):

    header:   i32 maxLength, then maxLength bytes (i32 version first)
    race:     f32 distanceDiffMax, i32 horseNum,
              i32 horseFrameSize (12), i32 horseResultSize (31)
    padding:  i32 size + size bytes           (before each block)
    frames:   i32 frameCount, i32 frameSize, then per frame:
                f32 time, then per horse:
                f32 distance, u16 lane (x1/10000), u16 speed (x1/100),
                u16 hp, i8 temptationMode, i8 blockFrontHorseIndex
    results:  horseNum x 31 bytes (finishOrder .. finishTimeRaw)
    events:   i32 eventCount, then per event:
                i16 eventSize, f32 frameTime, i8 type, i8 paramCount,
                paramCount x i32

Output deliberately mirrors the shape the desktop extractor produces
from live memory, so ``race_captures.replay_series`` consumes either
source unchanged. The one thing the blob does NOT contain is course
geometry (corners/slopes live on ``RaceManager``) — that stays a
live-capture bonus.
"""

from __future__ import annotations

import base64
import gzip
import struct

# Values verified from the Gallop.SimulateEventType enum dump; the blob
# stores the ordinal. Unknown ordinals pass through as "type_<n>" and
# are ignored downstream rather than raising — the JP client already
# has more members, and Global will inherit them.
_EVENT_TYPES = {
    0: "Score",
    1: "ChallengeMatchPoint",
    2: "NOUSE_2",
    3: "Skill",
    4: "CompeteTop",
    5: "CompeteFight",
    6: "ReleaseConservePower",
}

_HORSE_FRAME_SIZE = 12
_HORSE_RESULT_SIZE = 31
# Sanity bounds so a corrupt blob fails fast instead of allocating
# nonsense: no race has more runners, frames or events than these.
_MAX_HORSES = 32
_MAX_FRAMES = 5000
_MAX_EVENTS = 20000


class BlobParseError(Exception):
    pass


def _deobscure(hidden: bytes, crypto_key: str) -> str:
    """Undo CodeStage.AntiCheat ObscuredString: UTF-16LE code units,
    each XOR'd with the cycling ASCII key."""
    if not crypto_key:
        raise BlobParseError("empty crypto key")
    if len(hidden) % 2:
        raise BlobParseError("hiddenValue is not whole UTF-16 units")
    key = [ord(c) for c in crypto_key]
    out = []
    for i in range(0, len(hidden), 2):
        unit = hidden[i] | (hidden[i + 1] << 8)
        out.append(chr(unit ^ key[(i // 2) % len(key)]))
    return "".join(out)


def inflate(hidden: bytes, crypto_key: str) -> bytes:
    """Obscured memory bytes -> plaintext binary scenario."""
    text = _deobscure(hidden, crypto_key)
    try:
        packed = base64.b64decode(text, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise BlobParseError(f"not base64 after de-obscuring: {exc}") from exc
    if packed[:2] != b"\x1f\x8b":
        raise BlobParseError(f"expected gzip magic, got {packed[:4].hex()}")
    return gzip.decompress(packed)


def parse_replay(plain: bytes) -> dict:
    """Structured parse of the whole blob -> a live-memory-shaped sim.

    Raises BlobParseError on anything that doesn't look like the known
    layout; callers treat that as "no replay", never as a page error.
    """
    try:
        return _parse(plain)
    except BlobParseError:
        raise
    except Exception as exc:  # struct errors, index errors on truncation
        raise BlobParseError(f"malformed blob: {exc}") from exc


def _parse(plain: bytes) -> dict:
    (max_length,) = struct.unpack_from("<i", plain, 0)
    if not (0 <= max_length <= 64):
        raise BlobParseError(f"implausible header length {max_length}")
    off = 4 + max_length

    _ddm, horse_num, frame_rec, result_rec = struct.unpack_from("<fiii", plain, off)
    off += 16
    if not (1 <= horse_num <= _MAX_HORSES):
        raise BlobParseError(f"implausible horseNum {horse_num}")
    if frame_rec != _HORSE_FRAME_SIZE or result_rec != _HORSE_RESULT_SIZE:
        # A future client format change lands here — better to skip the
        # replay than misread quantized fields.
        raise BlobParseError(
            f"unknown record sizes frame={frame_rec} result={result_rec}"
        )

    (pad,) = struct.unpack_from("<i", plain, off)
    off += 4 + pad

    frame_count, frame_size = struct.unpack_from("<ii", plain, off)
    off += 8
    if not (0 < frame_count <= _MAX_FRAMES):
        raise BlobParseError(f"implausible frameCount {frame_count}")
    if frame_size != 4 + horse_num * frame_rec:
        raise BlobParseError(f"frameSize {frame_size} does not match horseNum")

    frames: list[dict] = []
    for i in range(frame_count):
        fo = off + i * frame_size
        (t,) = struct.unpack_from("<f", plain, fo)
        row = []
        for k in range(horse_num):
            d, lane, spd, hp, tempt, blk = struct.unpack_from(
                "<fHHHbb", plain, fo + 4 + k * frame_rec
            )
            row.append(
                {
                    "Distance": d,
                    "LanePosition": lane / 10000.0,
                    "Speed": spd / 100.0,
                    "Hp": float(hp),
                    "TemptationMode": tempt,
                    "BlockFrontHorseIndex": blk,
                }
            )
        frames.append({"t": t, "h": row})
    off += frame_count * frame_size

    (pad,) = struct.unpack_from("<i", plain, off)
    off += 4 + pad

    horses: list[dict] = []
    for _ in range(horse_num):
        fin, ft, fdt, sdt, guts, wiz, spurt = struct.unpack_from(
            "<ifffBBf", plain, off
        )
        style = plain[off + 22]
        (defeat,) = struct.unpack_from("<i", plain, off + 23)
        (ft_raw,) = struct.unpack_from("<f", plain, off + 27)
        horses.append(
            {
                "FinishOrder": fin,
                "FinishTime": ft,
                "FinishTimeRaw": ft_raw,
                "FinishDiffTime": fdt,
                "StartDelayTime": sdt,
                "GutsOrder": guts,
                "WizOrder": wiz,
                "LastSpurtStartDistance": spurt,
                "RunningStyle": style,
                "Defeat": defeat,
            }
        )
        off += result_rec

    (pad,) = struct.unpack_from("<i", plain, off)
    off += 4 + pad

    (event_count,) = struct.unpack_from("<i", plain, off)
    off += 4
    if not (0 <= event_count <= _MAX_EVENTS):
        raise BlobParseError(f"implausible eventCount {event_count}")

    events: list[dict] = []
    for _ in range(event_count):
        (esize,) = struct.unpack_from("<h", plain, off)
        t, etype, pcount = struct.unpack_from("<fbb", plain, off + 2)
        params = list(struct.unpack_from(f"<{pcount}i", plain, off + 8))
        events.append(
            {
                "t": t,
                "type": _EVENT_TYPES.get(etype, f"type_{etype}"),
                "param": params,
            }
        )
        off += 2 + esize

    return {
        "horseNum": horse_num,
        "lastCalcFrameTime": frames[-1]["t"] if frames else 0.0,
        "horses": horses,
        "frames": frames,
        "events": events,
    }


def sim_from_payload(payload: dict) -> dict | None:
    """The replay hiding in a capture payload's scenario, if any.

    Returns None rather than raising: a capture with a missing,
    truncated or future-format blob simply has no replay, exactly as if
    it had never carried one.
    """
    scenario = (payload or {}).get("scenario")
    if not isinstance(scenario, dict):
        return None
    key = scenario.get("key")
    raw_b64 = scenario.get("raw_b64")
    if not key or not raw_b64:
        return None
    try:
        hidden = base64.b64decode(raw_b64)
        return parse_replay(inflate(hidden, str(key)))
    except Exception:  # noqa: BLE001 — any failure means "no replay"
        return None
