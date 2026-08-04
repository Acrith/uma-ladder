"""Discovery scout: what race data is live in the client right now?

Answers two questions for the uma-ladder race extractor:
  1. Is the known Room Match target (WorkRoomMatchData) populated?
  2. Which other race/room-shaped singletons currently hold data?

Passive reads only — no hooks, no writes. Run with the game on
whatever screen you want to inventory.
"""
from __future__ import annotations

import sys
import time
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

      function liveCount(clsName) {
        try {
          const c = img.class(clsName);
          if (!c) return -1;
          const inst = Il2Cpp.gc.choose(c);
          return inst ? inst.length : 0;
        } catch (e) { return -2; }
      }

      // ── 1. Known Room Match target ──────────────────────────
      send({type: 'header', text: '=== Room Match target state ==='});
      const wrmName = 'Gallop.WorkRoomMatchData';
      const n = liveCount(wrmName);
      send({type: 'text', text: wrmName + ' live instances = ' + n});

      if (n > 0) {
        const wrm = Il2Cpp.gc.choose(img.class(wrmName))[0];
        const probes = [
          '_savedRaceResultInfo',
          '_raceResultInfo',
          '_currentRoomData',
          '_currentRoomUserList',
          '_roomMatchEntryCharaIdArray',
          '_deckDict',
        ];
        probes.forEach(fn => {
          let desc;
          try {
            const v = wrm.field(fn).value;
            if (v === null || v === undefined) desc = 'null';
            else if (typeof v === 'number' || typeof v === 'boolean') desc = String(v);
            else if (v.length !== undefined) desc = '<array len=' + v.length + '>';
            else if (v.class && v.class.type) desc = '<' + v.class.type.name + '>';
            else desc = '<' + (typeof v) + '>';
          } catch (e) { desc = '<err: ' + e.message + '>'; }
          send({type: 'text', text: '  .' + fn + ' = ' + desc});
        });

        // If a saved result is live, how many runners does it carry?
        try {
          const saved = wrm.field('_savedRaceResultInfo').value;
          if (saved) {
            const arr = saved.field('_raceHorseDataArray').value;
            send({type: 'text', text: '  -> _raceHorseDataArray len = ' +
                  (arr && arr.length !== undefined ? arr.length : 'n/a')});
            const rr = saved.field('_raceResult').value;
            if (rr) {
              ['room_name', 'race_instance_id', 'saved_room_id', 'entry_num',
               'start_time', 'season', 'weather', 'ground_condition'].forEach(k => {
                try {
                  const val = rr.field(k).value;
                  const s = (val && val.content !== undefined) ? val.content : val;
                  send({type: 'text', text: '     .' + k + ' = ' + s});
                } catch (e) {}
              });
            }
          }
        } catch (e) {
          send({type: 'text', text: '  -> saved walk err: ' + e.message});
        }
      }

      // ── 2. What else is live and race-shaped? ───────────────
      send({type: 'header', text: '=== live race/room singletons ==='});
      const pat = /^Gallop\.(Work|Race|Room|SingleMode)[A-Za-z]*(Data|Info|Manager|Response)$/;
      let scanned = 0, hits = 0;
      const classes = img.classes;
      for (let i = 0; i < classes.length; i++) {
        const c = classes[i];
        let nm;
        try { nm = c.type.name; } catch (e) { continue; }
        if (!pat.test(nm)) continue;
        scanned++;
        let cnt = 0;
        try {
          const inst = Il2Cpp.gc.choose(c);
          cnt = inst ? inst.length : 0;
        } catch (e) { continue; }
        if (cnt > 0) {
          hits++;
          send({type: 'text', text: '  ' + cnt + ' x ' + nm});
        }
      }
      send({type: 'text', text: '[scanned ' + scanned + ' matching classes, ' + hits + ' live]'});

      send({type: 'done'});
    } catch (e) {
      send({type: 'fatal', err: e.message, stack: e.stack});
    }
  });
}, 500);
"""


def _find_pid() -> int | None:
    import frida

    for proc in frida.get_local_device().enumerate_processes():
        if proc.name.lower() == PROCESS_NAME.lower():
            return proc.pid
    return None


def main() -> int:
    if not BRIDGE_JS.exists():
        print(f"[X] {BRIDGE_JS} missing")
        return 1
    try:
        import frida
    except ImportError:
        print("[X] frida not installed")
        return 1

    pid = _find_pid()
    if pid is None:
        print(f"[X] no {PROCESS_NAME} process found")
        return 1
    print(f"[+] attaching to PID {pid}")

    done = {"flag": False}

    def on_message(msg, data):
        if msg["type"] == "send":
            payload = msg["payload"]
            t = payload.get("type", "?")
            if t == "init":
                print("[+] IL2CPP ready")
            elif t in ("header", "text"):
                if t == "header":
                    print()
                print(payload["text"].encode("ascii", "replace").decode("ascii"))
            elif t == "done":
                print()
                print("[+] scout complete")
                done["flag"] = True
            elif t == "fatal":
                print(f"[X] agent error: {payload.get('err')}")
                print(payload.get("stack", ""))
                done["flag"] = True
        elif msg["type"] == "error":
            print(f"[X] JS runtime error: {msg.get('description')}")
            done["flag"] = True

    session = frida.attach(pid)
    src = BRIDGE_JS.read_text(encoding="utf-8") + "\n" + AGENT
    script = session.create_script(src)
    script.on("message", on_message)
    script.load()

    deadline = time.time() + 120
    while time.time() < deadline and not done["flag"]:
        time.sleep(0.1)
    if not done["flag"]:
        print("[X] scout didn't finish within 120s")
    session.detach()
    return 0 if done["flag"] else 1


if __name__ == "__main__":
    sys.exit(main())
