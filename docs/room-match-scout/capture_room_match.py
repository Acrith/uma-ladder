"""Capture one Room Match result — archive + upload payload.

This is the capture core that `uma-race-extract` will be built around.
It writes two files per race:

  <room>_<instance>.full.json    everything, including the raw scenario
                                 blob (base64). This is the analysis
                                 corpus — never throw it away, it is
                                 what lets a future decoder be re-run
                                 over every race we ever captured.

  <room>_<instance>.upload.json  the normalized, ladder-facing shape:
                                 room + runners + finishing order.

Every field of RaceHorseData is dumped generically rather than by an
allowlist, so a game update that adds a field cannot silently drop it
from the archive.

Run with the Room Match result loaded (the saved result survives
leaving the screen — see docs/room-match-extraction.md).

Usage:  py -3 capture_room_match.py [--out DIR]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
BRIDGE_JS = BASE_DIR / "il2cpp_bridge.js"
PROCESS_NAME = "UmamusumePrettyDerby.exe"

AGENT = r"""
setTimeout(() => {
  Il2Cpp.perform(() => {
    try {
      send({type: 'init'});
      const img = Il2Cpp.domain.assembly('umamusume').image;
      const found = Il2Cpp.gc.choose(img.class('Gallop.WorkRoomMatchData'));
      if (!found || !found.length) { send({type:'fatal', err:'no WorkRoomMatchData'}); return; }
      const wrm = found[0];
      const saved = wrm.field('_savedRaceResultInfo').value;
      if (!saved) { send({type:'fatal', err:'_savedRaceResultInfo is null - no saved race'}); return; }

      // Scalar-ish field reader. Never recurses into byte arrays -
      // that is what crashed the phase 3 scout.
      function plain(v) {
        try {
          if (v === null || v === undefined) return null;
          if (typeof v === 'number' || typeof v === 'boolean') return v;
          if (typeof v === 'string') return v;
          if (v.content !== undefined) return v.content;        // Il2Cpp.String
        } catch (e) { return null; }                            // bad pointer
        return undefined;                                        // object/array
      }

      function dumpScalars(obj) {
        const out = {};
        if (!obj || !obj.class || !obj.class.fields) return out;
        obj.class.fields.forEach(f => {
          if (f.isStatic || f.isLiteral || f.isThreadStatic) return;
          let v;
          try { v = obj.field(f.name).value; } catch (e) { out[f.name] = null; return; }
          const s = plain(v);
          if (s !== undefined) { out[f.name] = s; return; }
          try {
            if (v.length !== undefined) {                        // small int arrays
              if (v.length > 0 && v.length <= 8) {
                const arr = [];
                for (let i = 0; i < v.length; i++) {
                  let e = null;
                  try { e = plain(v.get(i)); } catch (err) { e = null; }
                  arr.push(e === undefined ? null : e);
                }
                out[f.name] = arr;
              } else {
                out[f.name] = {'_array_len': v.length};
              }
            }
          } catch (e) { out[f.name] = null; }
        });
        return out;
      }

      // ── room metadata ───────────────────────────────────────
      const room = dumpScalars(saved.field('_raceResult').value);

      // ── runners, every field + skill array ──────────────────
      const horses = saved.field('_raceHorseDataArray').value;
      const runners = [];
      for (let i = 0; i < horses.length; i++) {
        let rec = {};
        try {
          const h = horses.get(i);
          rec = dumpScalars(h);
          const skills = [];
          try {
            const sa = h.field('skill_array').value;
            for (let k = 0; k < sa.length; k++) {
              try {
                const sk = sa.get(k);
                if (!sk) continue;
                skills.push({
                  skill_id: sk.field('skill_id').value,
                  level: sk.field('level').value,
                });
              } catch (e) { /* skip this slot */ }
            }
          } catch (e) {}
          rec['skills'] = skills;
        } catch (e) {
          rec = {'_capture_error': String(e.message), 'skills': []};
        }
        runners.push(rec);
      }

      // ── raw scenario, shipped as binary ─────────────────────
      let key = null, scen_len = 0, raw = null;
      try {
        const scen = saved.field('_raceScenario').value;
        const ko = scen.field('currentCryptoKey').value;
        key = ko && ko.content !== undefined ? ko.content : String(ko);
        const hidden = scen.field('hiddenValue').value;
        scen_len = hidden.length;
        raw = hidden.elements.handle.readByteArray(scen_len);
      } catch (e) {
        send({type:'text', text:'scenario read failed: ' + e.message});
      }

      send({type: 'capture', room: room, runners: runners,
            scenario_key: key, scenario_len: scen_len}, raw);
      send({type: 'done'});
    } catch (e) {
      send({type:'fatal', err: e.message, stack: e.stack});
    }
  });
}, 500);
"""


def _normalize(full: dict) -> dict:
    """Ladder-facing payload: what the ingest endpoint consumes."""
    room = full["room"]
    runners = []
    results_by_gate = {r["gate"]: r for r in full.get("results", [])}
    for r in full["runners"]:
        gate = r.get("frame_order")
        res = results_by_gate.get(gate, {})
        runners.append(
            {
                "gate": gate,
                "trainer_name": r.get("trainer_name"),
                "chara_id": r.get("chara_id"),
                "card_id": r.get("card_id"),
                "finish_position": res.get("finish_position"),
                "finish_time_seconds": res.get("finish_time_seconds"),
                "gap_to_ahead_seconds": res.get("gap_to_ahead_seconds"),
                "running_style": r.get("running_style"),
                "popularity": r.get("popularity"),
                "stats": {
                    "speed": r.get("speed"),
                    "stamina": r.get("stamina"),
                    "power": r.get("pow"),
                    "guts": r.get("guts"),
                    "wit": r.get("wiz"),
                },
                "aptitudes": {
                    "turf": r.get("proper_ground_turf"),
                    "dirt": r.get("proper_ground_dirt"),
                    "sprint": r.get("proper_distance_short"),
                    "mile": r.get("proper_distance_mile"),
                    "medium": r.get("proper_distance_middle"),
                    "long": r.get("proper_distance_long"),
                    "front": r.get("proper_running_style_nige"),
                    "pace": r.get("proper_running_style_senko"),
                    "late": r.get("proper_running_style_sashi"),
                    "end": r.get("proper_running_style_oikomi"),
                },
                "skills": r.get("skills", []),
            }
        )
    runners.sort(key=lambda x: (x["finish_position"] is None, x["finish_position"] or 0))
    return {
        "schema": 1,
        "source": "memory_scan",
        "captured_at": full["captured_at"],
        "room": {
            "saved_room_id": room.get("saved_room_id"),
            "race_instance_id": room.get("race_instance_id"),
            "room_name": room.get("room_name"),
            "start_time": room.get("start_time"),
            "entry_num": room.get("entry_num"),
            "season": room.get("season"),
            "weather": room.get("weather"),
            "ground_condition": room.get("ground_condition"),
            "random_seed": room.get("random_seed"),
        },
        "runners": runners,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BASE_DIR / "captures"))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    import frida

    pid = None
    for proc in frida.get_local_device().enumerate_processes():
        if proc.name.lower() == PROCESS_NAME.lower():
            pid = proc.pid
            break
    if pid is None:
        print(f"[X] {PROCESS_NAME} not running")
        return 1
    print(f"[+] attaching to PID {pid}")

    state: dict = {"done": False, "payload": None, "raw": None}

    def on_message(msg, data):
        if msg["type"] == "send":
            p = msg["payload"]
            t = p.get("type")
            if t == "init":
                print("[+] IL2CPP ready")
            elif t == "text":
                print("   ", p["text"])
            elif t == "capture":
                state["payload"] = p
                state["raw"] = data
            elif t == "done":
                state["done"] = True
            elif t == "fatal":
                print(f"[X] {p.get('err')}")
                state["done"] = True
        elif msg["type"] == "error":
            print(f"[X] JS error: {msg.get('description')}")
            state["done"] = True

    session = frida.attach(pid)
    script = session.create_script(BRIDGE_JS.read_text(encoding="utf-8") + "\n" + AGENT)
    script.on("message", on_message)
    script.load()
    deadline = time.time() + 90
    while time.time() < deadline and not state["done"]:
        time.sleep(0.1)
    session.detach()

    p = state["payload"]
    if not p:
        print("[X] nothing captured")
        return 1

    room = p["room"]
    room_id = room.get("saved_room_id") or 0
    inst = room.get("race_instance_id") or 0
    stem = f"{room_id}_{inst}"

    full = {
        "schema": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": "memory_scan",
        "room": room,
        "runners": p["runners"],
        "scenario": {
            "key": p.get("scenario_key"),
            "len": p.get("scenario_len"),
            "raw_b64": base64.b64encode(state["raw"]).decode() if state["raw"] else None,
        },
    }

    # Decode the outcome if we can — keeps the archive self-describing.
    if state["raw"] and p.get("scenario_key"):
        try:
            sys.path.insert(0, str(BASE_DIR))
            from scenario_decode import decode

            res = decode(state["raw"], p["scenario_key"], len(p["runners"]))
            full["results"] = [r.__dict__ for r in res]
            print(f"[+] decoded finishing order for {len(res)} runners")
        except Exception as exc:  # noqa: BLE001
            print(f"[!] scenario decode failed (archived anyway): {exc}")
            full["results"] = []
    else:
        full["results"] = []

    (out_dir / f"{stem}.full.json").write_text(json.dumps(full, indent=2))
    (out_dir / f"{stem}.upload.json").write_text(json.dumps(_normalize(full), indent=2))

    # Room names legitimately contain fullwidth characters (the game's
    # "＃"), and the Windows console is cp1252 — never let a print kill
    # a capture that already succeeded.
    def _safe(x: object) -> str:
        return str(x).encode("ascii", "replace").decode("ascii")

    print(f"\n[+] room  : {_safe(room.get('room_name'))}  (saved_room_id={room_id})")
    print(f"[+] race  : instance {inst}, {len(p['runners'])} runners")
    print(f"[+] scen  : {p.get('scenario_len')} bytes")
    print(f"[+] wrote : {stem}.full.json  +  {stem}.upload.json  -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
