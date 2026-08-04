"""Find the game's OWN parser for the race scenario blob.

Three byte-level hypotheses have now been falsified (fixed-stride
samples, monotonic position tracks, float32 speed arrays). Rather than
keep guessing at the encoding, locate the class the game uses to
deserialize `_raceScenario` — its field layout *is* the format.
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
      send({type:'init'});
      const img = Il2Cpp.domain.assembly('umamusume').image;

      // 1. Classes whose name hints at scenario / simulation parsing.
      send({type:'header', text:'=== candidate classes ==='});
      const pat = /Scenario|Simulate|Replay/i;
      const names = [];
      const classes = img.classes;
      for (let i = 0; i < classes.length; i++) {
        let nm; try { nm = classes[i].type.name; } catch (e) { continue; }
        if (pat.test(nm)) names.push(nm);
      }
      names.sort();
      send({type:'text', text:'matched ' + names.length + ' classes'});
      names.slice(0, 60).forEach(nm => send({type:'text', text:'  ' + nm}));

      // 2. For the most promising, list fields + methods.
      const interesting = names.filter(nm =>
        /RaceSimulate|RaceScenario|SimulateData|ScenarioData/i.test(nm)).slice(0, 6);
      interesting.forEach(nm => {
        send({type:'header', text:'=== ' + nm + ' ==='});
        try {
          const c = img.class(nm);
          const fs = c.fields.filter(f => !f.isLiteral).slice(0, 40);
          send({type:'text', text:'fields (' + c.fields.length + '):'});
          fs.forEach(f => {
            let tn = '?'; try { tn = f.type.name; } catch (e) {}
            send({type:'text', text:'   .' + f.name + ' : ' + tn});
          });
          const ms = c.methods.slice(0, 30);
          send({type:'text', text:'methods (' + c.methods.length + '):'});
          ms.forEach(m => {
            let rt = '?'; try { rt = m.returnType.name; } catch (e) {}
            send({type:'text', text:'   ' + rt + ' ' + m.name + '(' + m.parameterCount + ')'});
          });
          const live = Il2Cpp.gc.choose(c);
          send({type:'text', text:'live instances: ' + (live ? live.length : 0)});
        } catch (e) {
          send({type:'text', text:'  err: ' + e.message});
        }
      });

      send({type:'done'});
    } catch (e) { send({type:'fatal', err:e.message, stack:e.stack}); }
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
        print("[X] game not running")
        return 1
    print(f"[+] attaching to PID {pid}")
    done = {"f": False}

    def on_message(msg, data):
        if msg["type"] == "send":
            p = msg["payload"]
            t = p.get("type")
            if t == "init":
                print("[+] IL2CPP ready")
            elif t in ("header", "text"):
                if t == "header":
                    print()
                print(p["text"].encode("ascii", "replace").decode("ascii"))
            elif t == "done":
                done["f"] = True
            elif t == "fatal":
                print("[X]", p.get("err"))
                done["f"] = True
        elif msg["type"] == "error":
            print("[X] JS:", msg.get("description"))
            done["f"] = True

    session = frida.attach(pid)
    s = session.create_script(BRIDGE_JS.read_text(encoding="utf-8") + "\n" + AGENT)
    s.on("message", on_message)
    s.load()
    deadline = time.time() + 120
    while time.time() < deadline and not done["f"]:
        time.sleep(0.1)
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
