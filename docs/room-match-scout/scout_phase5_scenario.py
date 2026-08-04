"""Phase 5: extract the _raceScenario blob.

Phase 4 proved the finishing order is NOT in RaceHorseData (full field
list has no outcome field), so the encrypted scenario is the only place
left for it. Phase 4's byte-array read used the wrong bridge API; this
tries several and ships the raw bytes to Python for offline analysis.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
BRIDGE_JS = BASE_DIR / "il2cpp_bridge.js"
PROCESS_NAME = "UmamusumePrettyDerby.exe"
OUT_RAW = BASE_DIR / "scenario_raw.bin"

AGENT = r"""
setTimeout(() => {
  Il2Cpp.perform(() => {
    try {
      send({type: 'init'});
      const img = Il2Cpp.domain.assembly('umamusume').image;
      const wrm = Il2Cpp.gc.choose(img.class('Gallop.WorkRoomMatchData'))[0];
      const saved = wrm.field('_savedRaceResultInfo').value;
      const scen = saved.field('_raceScenario').value;

      // What does the ObscuredString actually look like?
      send({type: 'header', text: '=== ObscuredString layout ==='});
      scen.class.fields.filter(f => !f.isStatic && !f.isLiteral).forEach(f => {
        let s;
        try {
          const v = scen.field(f.name).value;
          if (v === null || v === undefined) s = 'null';
          else if (v.content !== undefined) s = JSON.stringify(v.content);
          else if (v.length !== undefined) s = '<array len=' + v.length + '>';
          else s = String(v);
        } catch (e) { s = '<err ' + e.message + '>'; }
        send({type: 'text', text: '  .' + f.name + ' [' + (f.type && f.type.name) + '] = ' + s});
      });

      const keyObj = scen.field('currentCryptoKey').value;
      const key = keyObj && keyObj.content !== undefined ? keyObj.content : String(keyObj);
      const hidden = scen.field('hiddenValue').value;
      const len = hidden.length;
      send({type: 'text', text: 'key=' + JSON.stringify(key) + ' len=' + len});

      // ── Try progressively dumber ways to get the bytes out ──
      send({type: 'header', text: '=== byte read strategies ==='});
      let raw = null;

      // 1. bridge .elements (pointer wrapper)
      if (!raw) {
        try {
          raw = hidden.elements.handle.readByteArray(len);
          send({type: 'text', text: '[ok] .elements.handle.readByteArray'});
        } catch (e) { send({type: 'text', text: '[--] .elements.handle: ' + e.message}); }
      }
      // 2. raw IL2CPP array layout: obj hdr(0x10) + bounds(0x8) + length(0x8) = 0x20
      if (!raw) {
        try {
          raw = hidden.handle.add(0x20).readByteArray(len);
          send({type: 'text', text: '[ok] handle+0x20 readByteArray'});
        } catch (e) { send({type: 'text', text: '[--] handle+0x20: ' + e.message}); }
      }
      // 3. element-by-element (slow but always works)
      if (!raw) {
        try {
          const tmp = new Uint8Array(len);
          for (let i = 0; i < len; i++) tmp[i] = hidden.get(i) & 0xff;
          raw = tmp.buffer;
          send({type: 'text', text: '[ok] per-element .get()'});
        } catch (e) { send({type: 'text', text: '[--] per-element: ' + e.message}); }
      }

      if (raw) {
        const view = new Uint8Array(raw);
        let hex = '';
        for (let i = 0; i < Math.min(64, view.length); i++) {
          hex += ('0' + view[i].toString(16)).slice(-2) + ' ';
        }
        send({type: 'text', text: 'raw[0:64] = ' + hex});
        send({type: 'scenario_raw', len: view.length, key: key}, raw);
      } else {
        send({type: 'text', text: '[X] every strategy failed'});
      }

      send({type: 'done'});
    } catch (e) {
      send({type: 'fatal', err: e.message, stack: e.stack});
    }
  });
}, 500);
"""


def main() -> int:
    import frida

    pid = None
    for proc in frida.get_local_device().enumerate_processes():
        if proc.name.lower() == PROCESS_NAME.lower():
            pid = proc.pid
            break
    if pid is None:
        print(f"[X] no {PROCESS_NAME} process found")
        return 1
    print(f"[+] attaching to PID {pid}")

    done = {"flag": False}

    def on_message(msg, data):
        if msg["type"] == "send":
            p = msg["payload"]
            t = p.get("type", "?")
            if t == "init":
                print("[+] IL2CPP ready")
            elif t in ("header", "text"):
                if t == "header":
                    print()
                print(p["text"].encode("ascii", "replace").decode("ascii"))
            elif t == "scenario_raw":
                if data:
                    OUT_RAW.write_bytes(data)
                    print(f"[+] raw scenario saved ({len(data)} bytes) -> {OUT_RAW.name}")
                    (BASE_DIR / "scenario_key.txt").write_text(p.get("key", ""))
            elif t == "done":
                print("\n[+] scout complete")
                done["flag"] = True
            elif t == "fatal":
                print(f"[X] agent error: {p.get('err')}")
                print(p.get("stack", ""))
                done["flag"] = True
        elif msg["type"] == "error":
            print(f"[X] JS error: {msg.get('description')}")
            done["flag"] = True

    session = frida.attach(pid)
    script = session.create_script(BRIDGE_JS.read_text(encoding="utf-8") + "\n" + AGENT)
    script.on("message", on_message)
    script.load()

    deadline = time.time() + 120
    while time.time() < deadline and not done["flag"]:
        time.sleep(0.1)
    session.detach()
    return 0 if done["flag"] else 1


if __name__ == "__main__":
    sys.exit(main())
