"""uma-race-extract — save a Room Match result and send it to Uma Ladder.

Reads the race you just ran out of the game's memory: every
participant's uma, stats, aptitudes and skills, plus the finishing
order, times and margins. Nothing is written to the game and nothing
about it is changed — this only reads.

For players (Windows):

  1. Finish a Room Match (or open a saved result / replay)
  2. Double-click uma-race-extract.exe
  3. A JSON file appears in captures/ next to the exe

The saved result survives leaving the result screen, so there is no
need to sit on a particular screen. If a replay happens to be open,
the full per-frame race telemetry is captured too.

This tool talks to umaladder.moe. The sibling IT recorder talks to
training.umaladder.moe and keeps its settings in uma-it-config.json;
this one uses uma-race-config.json, so the two can share a folder
without ever reading each other's token.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
HOME_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
BRIDGE_JS = BASE_DIR / "vendor" / "il2cpp_bridge.js"
CONFIG_PATH = HOME_DIR / "uma-race-config.json"
CAPTURE_DIR = HOME_DIR / "captures"
PROCESS_NAME = "UmamusumePrettyDerby.exe"
DEFAULT_API_URL = "https://umaladder.moe"
WAIT_FOR_GAME_SECONDS = 300

AGENT = r"""
setTimeout(() => {
  Il2Cpp.perform(() => {
    try {
      send({type: 'init'});
      const img = Il2Cpp.domain.assembly('umamusume').image;
      const found = Il2Cpp.gc.choose(img.class('Gallop.WorkRoomMatchData'));
      if (!found || !found.length) { send({type:'fatal', err:'NO_ROOM_DATA'}); return; }
      const wrm = found[0];
      const saved = wrm.field('_savedRaceResultInfo').value;
      if (!saved) { send({type:'fatal', err:'NO_SAVED_RESULT'}); return; }

      function plain(v) {
        try {
          if (v === null || v === undefined) return null;
          if (typeof v === 'number' || typeof v === 'boolean') return v;
          if (typeof v === 'string') return v;
          if (v.content !== undefined) return v.content;
        } catch (e) { return null; }
        return undefined;
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
            if (v.length !== undefined) {
              if (v.length > 0 && v.length <= 8) {
                const arr = [];
                for (let i = 0; i < v.length; i++) {
                  let e = null;
                  try { e = plain(v.get(i)); } catch (err) { e = null; }
                  arr.push(e === undefined ? null : e);
                }
                out[f.name] = arr;
              } else { out[f.name] = {'_array_len': v.length}; }
            }
          } catch (e) { out[f.name] = null; }
        });
        return out;
      }

      const room = dumpScalars(saved.field('_raceResult').value);

      // One bad runner must never cost us the other seventeen.
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
                skills.push({skill_id: sk.field('skill_id').value,
                             level: sk.field('level').value});
              } catch (e) {}
            }
          } catch (e) {}
          rec['skills'] = skills;
        } catch (e) { rec = {'_capture_error': String(e.message), 'skills': []}; }
        runners.push(rec);
      }

      let key = null, scen_len = 0, raw = null;
      try {
        const scen = saved.field('_raceScenario').value;
        const ko = scen.field('currentCryptoKey').value;
        key = ko && ko.content !== undefined ? ko.content : String(ko);
        const hidden = scen.field('hiddenValue').value;
        scen_len = hidden.length;
        raw = hidden.elements.handle.readByteArray(scen_len);
      } catch (e) {}

      // Opportunistic: only live while a replay is loaded. Its absence
      // is normal and must not fail the capture.
      let sim = null;
      try {
        const sd = Il2Cpp.gc.choose(img.class('Gallop.RaceSimulateData'))[0];
        if (sd) {
          sim = {horseNum: sd.field('_horseNum').value,
                 lastFrameIdx: sd.field('_lastFrameIdx').value,
                 lastCalcFrameTime: sd.field('_lastCalcFrameTime').value,
                 horses: [], frames: []};
          const hr = sd.field('_horseResultDataArray').value;
          for (let i = 0; i < hr.length; i++) {
            const h = hr.get(i); const r = {};
            ['FinishOrder','FinishTime','FinishTimeRaw','FinishDiffTime',
             'StartDelayTime','GutsOrder','WizOrder','LastSpurtStartDistance',
             'RunningStyle','Defeat'].forEach(n => {
              try { r[n] = h.field(n).value; } catch (e) { r[n] = null; }
            });
            sim.horses.push(r);
          }
          const fl = sd.field('_frameDataList').value;
          const size = fl.field('_size').value;
          const items = fl.field('_items').value;
          for (let i = 0; i < size; i++) {
            const fr = items.get(i);
            const hda = fr.field('HorseDataArray').value;
            const row = {t: fr.field('Time').value, h: []};
            for (let k = 0; k < hda.length; k++) {
              const hh = hda.get(k);
              const rec = {};
              ['Distance','LanePosition','Speed','Hp','TemptationMode',
               'BlockFrontHorseIndex'].forEach(n => {
                try { rec[n] = hh.field(n).value; } catch (e) { rec[n] = null; }
              });
              row.h.push(rec);
            }
            sim.frames.push(row);
          }
        }
      } catch (e) { sim = null; }

      send({type: 'capture', room: room, runners: runners, sim: sim,
            scenario_key: key, scenario_len: scen_len}, raw);
      send({type: 'done'});
    } catch (e) { send({type:'fatal', err: e.message, stack: e.stack}); }
  });
}, 500);
"""


# ─── config ──────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(token: str, api_url: str = DEFAULT_API_URL) -> None:
    CONFIG_PATH.write_text(
        json.dumps({"api_url": api_url, "token": token}, indent=2), encoding="utf-8"
    )


def first_run_prompt() -> dict:
    """Ask once, then never nag again — the config is written whatever
    the answer, so declining is remembered too."""
    print()
    print("-" * 62)
    print(" Upload captures to Uma Ladder (umaladder.moe)?")
    print()
    print(" Get a token from your account page. Leave blank to skip -")
    print(" captures are always saved locally either way.")
    print()
    print(" NOTE: this is umaladder.moe, not training.umaladder.moe.")
    print(" A token from the IT recorder will not work here.")
    print("-" * 62)
    try:
        token = input(" token> ").strip()
    except (EOFError, KeyboardInterrupt):
        token = ""
    write_config(token)
    if token:
        print(" saved. Uploads are on.")
    else:
        print(" saved. Local-only; add a token to uma-race-config.json to enable uploads.")
    print()
    return load_config()


# ─── capture ─────────────────────────────────────────────────────────

def find_pid(wait_seconds: int = 0):
    import frida

    deadline = time.time() + wait_seconds
    announced = False
    while True:
        for proc in frida.get_local_device().enumerate_processes():
            if proc.name.lower() == PROCESS_NAME.lower():
                return proc.pid
        if time.time() >= deadline:
            return None
        if not announced:
            print(f"[.] waiting for {PROCESS_NAME} (up to {wait_seconds}s)...")
            announced = True
        time.sleep(2)


def capture(pid: int) -> dict | None:
    import frida

    state: dict = {"done": False, "payload": None, "raw": None, "err": None}

    def on_message(msg, data):
        if msg["type"] == "send":
            p = msg["payload"]
            t = p.get("type")
            if t == "capture":
                state["payload"] = p
                state["raw"] = data
            elif t == "done":
                state["done"] = True
            elif t == "fatal":
                state["err"] = p.get("err")
                state["done"] = True
        elif msg["type"] == "error":
            state["err"] = msg.get("description")
            state["done"] = True

    session = frida.attach(pid)
    try:
        script = session.create_script(
            BRIDGE_JS.read_text(encoding="utf-8") + "\n" + AGENT
        )
        script.on("message", on_message)
        script.load()
        deadline = time.time() + 120
        while time.time() < deadline and not state["done"]:
            time.sleep(0.1)
    finally:
        session.detach()

    if state["err"] == "NO_SAVED_RESULT":
        print("[X] no saved Room Match result in memory.")
        print("    Run a Room Match, or open a saved result, then try again.")
        return None
    if state["err"]:
        print(f"[X] capture failed: {state['err']}")
        return None
    if not state["payload"]:
        print("[X] capture produced nothing")
        return None

    p = state["payload"]
    full = {
        "schema": 1,
        "source": "memory_scan",
        "captured_at": datetime.now(UTC).isoformat(),
        "room": p["room"],
        "runners": p["runners"],
        "scenario": {
            "key": p.get("scenario_key"),
            "len": p.get("scenario_len"),
            "raw_b64": base64.b64encode(state["raw"]).decode() if state["raw"] else None,
        },
        "sim": p.get("sim"),
    }
    if state["raw"] and p.get("scenario_key"):
        try:
            sys.path.insert(0, str(BASE_DIR))
            from scenario_decode import decode

            full["results"] = [r.__dict__ for r in
                               decode(state["raw"], p["scenario_key"], len(p["runners"]))]
        except Exception as exc:  # noqa: BLE001
            print(f"[!] could not decode the finishing order ({exc}); "
                  "the raw scenario is archived so it can be decoded later")
            full["results"] = []
    else:
        full["results"] = []
    return full


def to_upload_payload(full: dict) -> dict:
    """The ladder-facing shape. Keeps the raw scenario so the site
    accumulates the corpus that future decoding work needs."""
    room = full["room"]
    by_gate = {r["gate"]: r for r in full.get("results", [])}
    runners = []
    for r in full["runners"]:
        gate = r.get("frame_order")
        res = by_gate.get(gate, {})
        runners.append({
            "gate": gate,
            "trainer_name": r.get("trainer_name"),
            "chara_id": r.get("chara_id"),
            "card_id": r.get("card_id"),
            "finish_position": res.get("finish_position"),
            "finish_time_seconds": res.get("finish_time_seconds"),
            "gap_to_ahead_seconds": res.get("gap_to_ahead_seconds"),
            "running_style": r.get("running_style"),
            "popularity": r.get("popularity"),
            "stats": {"speed": r.get("speed"), "stamina": r.get("stamina"),
                      "power": r.get("pow"), "guts": r.get("guts"), "wit": r.get("wiz")},
            "aptitudes": {
                "turf": r.get("proper_ground_turf"), "dirt": r.get("proper_ground_dirt"),
                "sprint": r.get("proper_distance_short"), "mile": r.get("proper_distance_mile"),
                "medium": r.get("proper_distance_middle"), "long": r.get("proper_distance_long"),
                "front": r.get("proper_running_style_nige"),
                "pace": r.get("proper_running_style_senko"),
                "late": r.get("proper_running_style_sashi"),
                "end": r.get("proper_running_style_oikomi"),
            },
            "skills": r.get("skills", []),
        })
    runners.sort(key=lambda x: (x["finish_position"] is None, x["finish_position"] or 0))
    return {
        "schema": 1,
        "source": "memory_scan",
        "captured_at": full["captured_at"],
        "room": {k: room.get(k) for k in (
            "saved_room_id", "race_instance_id", "room_name", "start_time",
            "entry_num", "season", "weather", "ground_condition", "random_seed")},
        "runners": runners,
        "scenario": full.get("scenario"),
        "sim": full.get("sim"),
    }


def upload(payload: dict, cfg: dict) -> bool:
    api_url = (cfg.get("api_url") or DEFAULT_API_URL).rstrip("/")
    token = cfg.get("token")
    if not token:
        return False
    req = urlrequest.Request(
        f"{api_url}/api/race-captures",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("created"):
            print(f"[+] uploaded to {api_url} - review it at "
                  f"{api_url}{body.get('review_url', '')}")
        else:
            print(f"[+] already on {api_url} (someone in the room uploaded it) - "
                  f"{api_url}{body.get('review_url', '')}")
        return True
    except urlerror.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        if exc.code == 401:
            print("[X] upload rejected: token invalid or revoked.")
            print(f"    Check uma-race-config.json - the token must be from {api_url},")
            print("    not from training.umaladder.moe.")
        else:
            print(f"[X] upload failed ({exc.code}) {detail}")
    except (urlerror.URLError, TimeoutError) as exc:
        print(f"[X] could not reach {api_url}: {exc}")
    print("    The capture is saved locally and can be uploaded later.")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture a Room Match result for Uma Ladder.")
    ap.add_argument("--no-upload", action="store_true", help="save locally only")
    ap.add_argument("--wait", type=int, default=WAIT_FOR_GAME_SECONDS,
                    help="seconds to wait for the game to start")
    args = ap.parse_args()

    print("uma-race-extract - Uma Ladder race recorder (umaladder.moe)")
    try:
        import frida  # noqa: F401
    except ImportError:
        print("[X] frida is not installed")
        return 1

    cfg = load_config()
    if not CONFIG_PATH.exists():
        cfg = first_run_prompt()

    pid = find_pid(args.wait)
    if pid is None:
        print(f"[X] {PROCESS_NAME} is not running")
        return 1
    print(f"[+] reading from the game (pid {pid})")

    full = capture(pid)
    if full is None:
        return 1

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    room = full["room"]
    stem = f"{room.get('saved_room_id') or 0}_{room.get('race_instance_id') or 0}"
    # Local archive first, always: a failed upload must never cost a race.
    (CAPTURE_DIR / f"{stem}.json").write_text(json.dumps(full, indent=2), encoding="utf-8")

    name = str(room.get("room_name") or "?").encode("ascii", "replace").decode("ascii")
    print(f"[+] {name}: {len(full['runners'])} runners", end="")
    if full.get("results"):
        winner = min(full["results"], key=lambda r: r["finish_position"])
        who = next((r.get("trainer_name") for r in full["runners"]
                    if r.get("frame_order") == winner["gate"]), "?")
        who = str(who).encode("ascii", "replace").decode("ascii")
        print(f", won by {who} in {winner['finish_time_seconds']}s")
    else:
        print(" (finishing order unavailable)")
    if full.get("sim"):
        print(f"[+] replay captured too: {len(full['sim']['frames'])} frames")
    print(f"[+] saved {CAPTURE_DIR / (stem + '.json')}")

    if not args.no_upload:
        upload(to_upload_payload(full), cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
