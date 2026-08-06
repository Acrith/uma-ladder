"""Dump the replay's event stream + whatever course geometry is reachable.

Needs a replay loaded (RaceSimulateData only lives while one is open).

Three questions this answers:
  1. What are SimulateEventType's members? (skill activation, spurt, ...)
  2. What does each event's param[] mean? -> skill id, target, duration?
  3. Does the client hold corner/straight/slope geometry for the course?
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

    // ---- helpers ------------------------------------------------------
    function safe(fn, dflt) { try { return fn(); } catch (e) { return dflt; } }
    function fieldNames(klass) {
      return klass.fields.filter(f => !f.isStatic && !f.isLiteral).map(f => f.name);
    }
    // Turn any il2cpp value into something JSON can carry.
    function plain(v, depth) {
      depth = depth || 0;
      if (v === null || v === undefined) return null;
      if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'string') return v;
      if (v.content !== undefined) return v.content;            // Il2Cpp.String
      if (v.length !== undefined) {                             // Il2Cpp.Array
        if (depth > 1) return '<arr len ' + v.length + '>';
        const a = [];
        for (let i = 0; i < Math.min(v.length, 64); i++) a.push(safe(() => plain(v.get(i), depth + 1), null));
        if (v.length > 64) a.push('...+' + (v.length - 64));
        return a;
      }
      if (v.class) {
        if (depth > 1) return '<' + safe(() => v.class.type.name, 'obj') + '>';
        const o = {};
        fieldNames(v.class).forEach(n => { o[n] = safe(() => plain(v.field(n).value, depth + 1), null); });
        return o;
      }
      return String(v);
    }

    // ---- 1. the SimulateEventType enum --------------------------------
    out.eventTypeEnum = {};
    ['Gallop.SimulateEventType', 'Gallop.RaceDefine.SimulateEventType', 'SimulateEventType'].forEach(cn => {
      safe(() => {
        const k = img.class(cn);
        k.fields.filter(f => f.isLiteral).forEach(f => {
          out.eventTypeEnum[f.name] = safe(() => f.value.valueOf ? f.value.valueOf() : f.value, null);
        });
        if (Object.keys(out.eventTypeEnum).length) out.eventTypeEnumClass = cn;
      });
    });

    // ---- 2. RaceSimulateData: full field inventory + the event list ----
    const sd = Il2Cpp.gc.choose(img.class('Gallop.RaceSimulateData'))[0];
    if (!sd) { send({type:'fatal', err:'no live RaceSimulateData - open a replay first'}); return; }

    out.simDataFields = [];
    sd.class.fields.filter(f => !f.isStatic && !f.isLiteral).forEach(f => {
      const tn = safe(() => f.type.name, '?');
      let val = null;
      safe(() => {
        const v = sd.field(f.name).value;
        val = (v === null || v === undefined) ? null
            : (typeof v === 'number' || typeof v === 'boolean') ? v
            : (v.content !== undefined) ? v.content
            : (v.length !== undefined) ? ('<arr len ' + v.length + '>')
            : ('<' + safe(() => v.class.type.name, 'obj') + '>');
      });
      out.simDataFields.push({name: f.name, type: tn, sample: val});
    });

    out.horseNum = safe(() => sd.field('_horseNum').value, null);

    const el = safe(() => sd.field('_simEvDataList').value, null);
    if (el) {
      const size = el.field('_size').value;
      const items = el.field('_items').value;
      out.eventCount = size;
      // Field shape of one event, so we know what we are reading.
      if (size > 0) {
        const e0 = items.get(0);
        out.eventClass = safe(() => e0.class.type.name, '?');
        out.eventFieldTypes = e0.class.fields.filter(f => !f.isStatic && !f.isLiteral)
          .map(f => ({name: f.name, type: safe(() => f.type.name, '?')}));
      }
      // Every event, fully expanded.
      out.events = [];
      for (let i = 0; i < size; i++) {
        const ev = items.get(i);
        out.events.push(safe(() => plain(ev, 0), {_err: i}));
      }
    }

    // ---- 3. course geometry -------------------------------------------
    // Anything the client knows about corners/straights/slopes for this course.
    out.courseCandidates = [];
    ['Gallop.RaceCourseData', 'Gallop.CourseData', 'Gallop.RaceCourseSet',
     'Gallop.RaceCourseParam', 'Gallop.CourseDataInfo', 'Gallop.RaceCourseInfo',
     'Gallop.RaceSimulateHorseResultData'].forEach(cn => {
      safe(() => {
        const k = img.class(cn);
        const insts = Il2Cpp.gc.choose(k);
        const rec = {klass: cn, live: insts.length,
                     fields: k.fields.filter(f => !f.isStatic && !f.isLiteral)
                              .map(f => ({name: f.name, type: safe(() => f.type.name, '?')}))};
        if (insts.length) rec.sample = safe(() => plain(insts[0], 0), null);
        out.courseCandidates.push(rec);
      });
    });

    // Broad sweep: any Gallop class whose name mentions course/corner/slope.
    out.courseClassNames = [];
    safe(() => {
      img.classes.forEach(k => {
        const n = k.type.name;
        if (/course|corner|slope|straight/i.test(n)) out.courseClassNames.push(n);
      });
    });

    send({type:'events', data: out});
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
        if p.get("type") == "events":
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
dl = time.time() + 180
while time.time() < dl and not st["f"]:
    time.sleep(0.1)
s.detach()

if st["d"]:
    p = BASE_DIR / "events.json"
    p.write_text(json.dumps(st["d"], indent=2), encoding="utf-8")
    d = st["d"]
    print(f"[+] wrote {p.name}")
    print(f"    enum members : {len(d.get('eventTypeEnum') or {})}")
    print(f"    events       : {d.get('eventCount')}")
    print(f"    course names : {len(d.get('courseClassNames') or [])}")
else:
    print("[X] nothing captured")
