"""Pull the course geometry: straights, corners, slopes for the loaded race.

The class sweep found Gallop.CourseStraight / CourseCorner / CourseSlope.
This finds who owns them and reads the actual metre positions, so the
replay track can shade corners and hills instead of guessing.
"""
from __future__ import annotations
import json, time
from pathlib import Path

BASE_DIR = Path(__file__).parent
BRIDGE_JS = BASE_DIR / ".." / ".." / "tools" / "race_extractor" / "vendor" / "il2cpp_bridge.js"

AGENT = r"""
setTimeout(() => { Il2Cpp.perform(() => {
  try {
    send({type:'init'});
    const img = Il2Cpp.domain.assembly('umamusume').image;
    const out = {};
    function safe(fn, d) { try { return fn(); } catch (e) { return d; } }
    function fnames(k) { return k.fields.filter(f=>!f.isStatic&&!f.isLiteral).map(f=>f.name); }
    function plain(v, depth) {
      depth = depth || 0;
      if (v === null || v === undefined) return null;
      if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'string') return v;
      if (v.content !== undefined) return v.content;
      if (v.length !== undefined) {
        if (depth > 2) return '<arr ' + v.length + '>';
        const a = [];
        for (let i = 0; i < Math.min(v.length, 40); i++) a.push(safe(()=>plain(v.get(i), depth+1), null));
        if (v.length > 40) a.push('...+' + (v.length - 40));
        return a;
      }
      if (v.class) {
        if (depth > 2) return '<' + safe(()=>v.class.type.name,'obj') + '>';
        const o = {};
        fnames(v.class).forEach(n => { o[n] = safe(()=>plain(v.field(n).value, depth+1), null); });
        return o;
      }
      return String(v);
    }

    // ---- 1. the geometry leaf classes ---------------------------------
    out.geom = {};
    ['Gallop.CourseStraight', 'Gallop.CourseCorner', 'Gallop.CourseSlope'].forEach(cn => {
      safe(() => {
        const k = img.class(cn);
        const insts = Il2Cpp.gc.choose(k);
        out.geom[cn] = {
          live: insts.length,
          fields: k.fields.filter(f=>!f.isStatic&&!f.isLiteral)
                   .map(f => ({name: f.name, type: safe(()=>f.type.name,'?')})),
          samples: insts.slice(0, 30).map(i => safe(()=>plain(i, 1), null)),
        };
      });
    });

    // ---- 2. who owns them? --------------------------------------------
    // Any class with a field whose type mentions CourseStraight/Corner/Slope.
    out.owners = [];
    safe(() => {
      img.classes.forEach(k => {
        safe(() => {
          const hits = k.fields.filter(f => {
            const tn = safe(()=>f.type.name, '');
            return /CourseStraight|CourseCorner|CourseSlope/.test(tn);
          });
          if (hits.length) {
            out.owners.push({
              klass: safe(()=>k.type.name,'?'),
              live: safe(()=>Il2Cpp.gc.choose(k).length, -1),
              fields: hits.map(f => ({name: f.name, type: safe(()=>f.type.name,'?')})),
            });
          }
        });
      });
    });

    // ---- 3. read the owner that is actually live ----------------------
    out.courseData = [];
    out.owners.forEach(o => {
      if (o.live > 0 && out.courseData.length < 4) {
        safe(() => {
          const k = img.class(o.klass);
          const inst = Il2Cpp.gc.choose(k)[0];
          const rec = {klass: o.klass, all: {}};
          fnames(k).forEach(n => { rec.all[n] = safe(()=>plain(inst.field(n).value, 0), null); });
          out.courseData.push(rec);
        });
      }
    });

    // ---- 4. the simulate header (race identity) ------------------------
    safe(() => {
      const sd = Il2Cpp.gc.choose(img.class('Gallop.RaceSimulateData'))[0];
      if (sd) {
        const h = sd.field('<Header>k__BackingField').value;
        out.header = safe(()=>plain(h, 0), null);
      }
    });

    send({type:'course', data: out});
    send({type:'done'});
  } catch (e) { send({type:'fatal', err: e.message, stack: e.stack}); }
}); }, 500);
"""

import frida

pid = next((p.pid for p in frida.get_local_device().enumerate_processes()
            if p.name.lower() == "umamusumeprettyderby.exe"), None)
if pid is None:
    raise SystemExit("[X] game is not running")
print(f"[+] attaching {pid}")

st = {"f": False, "d": None}


def om(m, d):
    if m["type"] == "send":
        p = m["payload"]
        if p.get("type") == "course":
            st["d"] = p["data"]
        elif p.get("type") == "done":
            st["f"] = True
        elif p.get("type") == "fatal":
            print("[X]", p.get("err"))
            print(p.get("stack") or "")
            st["f"] = True
    elif m["type"] == "error":
        print("[X]", m.get("description"))
        st["f"] = True


s = frida.attach(pid)
sc = s.create_script(BRIDGE_JS.read_text(encoding="utf-8") + "\n" + AGENT)
sc.on("message", om)
sc.load()
dl = time.time() + 300
while time.time() < dl and not st["f"]:
    time.sleep(0.1)
s.detach()

if st["d"]:
    p = BASE_DIR / "course.json"
    p.write_text(json.dumps(st["d"], indent=2), encoding="utf-8")
    print(f"[+] wrote {p.name}")
    for cn, g in (st["d"].get("geom") or {}).items():
        print(f"    {cn}: {g['live']} live")
    print(f"    owners: {len(st['d'].get('owners') or [])}")
else:
    print("[X] nothing captured")
