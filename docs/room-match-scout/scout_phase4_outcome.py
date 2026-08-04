"""Phase 4 scout: find the race OUTCOME (finishing order).

The doc's two open blockers:
  A) every horse's .race_result_array came back len=0 — so where do
     placements live?
  B) ._raceScenario is a ~31 KB CodeStage ObscuredString — does the
     XOR decode produce msgpack, and does it carry finish order?

Also dumps _currentRoomData / _currentRoomUserList, which the phase 3
scout crashed before reaching.

Passive reads only. Writes the decoded scenario blob next to this
script for offline analysis.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
BRIDGE_JS = BASE_DIR / "il2cpp_bridge.js"
PROCESS_NAME = "UmamusumePrettyDerby.exe"
OUT_SCENARIO = BASE_DIR / "scenario_decoded.bin"
OUT_SCENARIO_RAW = BASE_DIR / "scenario_raw.bin"

AGENT = r"""
setTimeout(() => {
  Il2Cpp.perform(() => {
    try {
      send({type: 'init'});
      const img = Il2Cpp.domain.assembly('umamusume').image;
      const wrm = Il2Cpp.gc.choose(img.class('Gallop.WorkRoomMatchData'))[0];
      const saved = wrm.field('_savedRaceResultInfo').value;

      // ── A. race_result_array per horse ──────────────────────
      send({type: 'header', text: '=== A. per-horse race_result_array ==='});
      const horses = saved.field('_raceHorseDataArray').value;
      for (let i = 0; i < horses.length; i++) {
        const h = horses.get(i);
        let name = '?';
        try { name = h.field('trainer_name').value.content; } catch (e) {}
        const bits = [];
        // Any field that might carry the outcome.
        ['race_result_array', 'finish_order', 'result_rank', 'order',
         'finish_time', 'final_grade', 'popularity', 'frame_order'].forEach(fn => {
          try {
            const v = h.field(fn).value;
            if (v === null || v === undefined) { bits.push(fn + '=null'); return; }
            if (v.length !== undefined) { bits.push(fn + '=<len ' + v.length + '>'); return; }
            bits.push(fn + '=' + v);
          } catch (e) { /* field absent */ }
        });
        send({type: 'text', text: '  [' + i + '] ' + name + ' :: ' + bits.join('  ')});
      }

      // Enumerate ALL field names on RaceHorseData once, so we can see
      // if an outcome-ish field exists that we haven't guessed.
      send({type: 'header', text: '=== A2. all RaceHorseData field names ==='});
      try {
        const h0 = horses.get(0);
        const names = h0.class.fields
          .filter(f => !f.isStatic && !f.isLiteral)
          .map(f => f.name);
        send({type: 'text', text: names.join(', ')});
      } catch (e) {
        send({type: 'text', text: 'err: ' + e.message});
      }

      // ── B. _raceScenario ObscuredString ─────────────────────
      send({type: 'header', text: '=== B. _raceScenario ==='});
      try {
        const scen = saved.field('_raceScenario').value;
        const keyObj = scen.field('currentCryptoKey').value;
        const key = keyObj && keyObj.content !== undefined ? keyObj.content : String(keyObj);
        const hidden = scen.field('hiddenValue').value;
        send({type: 'text', text: 'key = ' + JSON.stringify(key) + ' len=' + key.length});
        send({type: 'text', text: 'hiddenValue len = ' + hidden.length});

        // Read the whole byte array out of memory in one go.
        const raw = hidden.elements.readByteArray(hidden.length);
        send({type: 'scenario_raw', len: hidden.length, key: key}, raw);

        // XOR against the cycling key — the standard CodeStage undo.
        const bytes = new Uint8Array(raw);
        const keyBytes = [];
        for (let i = 0; i < key.length; i++) keyBytes.push(key.charCodeAt(i) & 0xff);
        const out = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) {
          out[i] = bytes[i] ^ keyBytes[i % keyBytes.length];
        }
        // First bytes as hex, to eyeball the format.
        let hex = '';
        for (let i = 0; i < Math.min(48, out.length); i++) {
          hex += ('0' + out[i].toString(16)).slice(-2) + ' ';
        }
        send({type: 'text', text: 'xor[0:48] = ' + hex});
        send({type: 'scenario_decoded', len: out.length}, out.buffer);
      } catch (e) {
        send({type: 'text', text: 'scenario err: ' + e.message});
      }

      // ── C. _currentRoomData (phase 3 never got here) ────────
      send({type: 'header', text: '=== C. _currentRoomData ==='});
      try {
        const room = wrm.field('_currentRoomData').value;
        room.class.fields.filter(f => !f.isStatic && !f.isLiteral).forEach(f => {
          try {
            const v = room.field(f.name).value;
            let s;
            if (v === null || v === undefined) s = 'null';
            else if (v.content !== undefined) s = JSON.stringify(v.content);
            else if (v.length !== undefined) s = '<len ' + v.length + '>';
            else if (v.class && v.class.type) s = '<' + v.class.type.name + '>';
            else s = String(v);
            send({type: 'text', text: '  .' + f.name + ' = ' + s});
          } catch (e) {}
        });
      } catch (e) {
        send({type: 'text', text: 'room err: ' + e.message});
      }

      // ── D. _currentRoomUserList — viewer_id -> player identity ──
      send({type: 'header', text: '=== D. _currentRoomUserList ==='});
      try {
        const ul = wrm.field('_currentRoomUserList').value;
        const size = ul.field('_size').value;
        const items = ul.field('_items').value;
        send({type: 'text', text: 'size = ' + size});
        for (let i = 0; i < Math.min(size, 12); i++) {
          const u = items.get(i);
          if (!u) continue;
          const bits = [];
          u.class.fields.filter(f => !f.isStatic && !f.isLiteral).forEach(f => {
            try {
              const v = u.field(f.name).value;
              let s;
              if (v === null || v === undefined) s = 'null';
              else if (v.content !== undefined) s = JSON.stringify(v.content);
              else if (v.length !== undefined) s = '<len ' + v.length + '>';
              else if (v.class && v.class.type) s = '<' + v.class.type.name + '>';
              else s = String(v);
              bits.push(f.name + '=' + s);
            } catch (e) {}
          });
          send({type: 'text', text: '  [' + i + '] ' + bits.join('  ')});
        }
      } catch (e) {
        send({type: 'text', text: 'userlist err: ' + e.message});
      }

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
    import frida

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
            elif t == "scenario_raw":
                if data:
                    OUT_SCENARIO_RAW.write_bytes(data)
                    print(f"[+] raw scenario saved ({len(data)} bytes) -> {OUT_SCENARIO_RAW.name}")
            elif t == "scenario_decoded":
                if data:
                    OUT_SCENARIO.write_bytes(data)
                    print(f"[+] xor-decoded scenario saved ({len(data)} bytes) -> {OUT_SCENARIO.name}")
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
