"""Read the actual per-runner, per-frame replay leaf data."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
BASE_DIR = Path(__file__).parent
BRIDGE_JS = BASE_DIR / "il2cpp_bridge.js"
AGENT = r"""
setTimeout(() => { Il2Cpp.perform(() => {
  try {
    send({type:'init'});
    const img = Il2Cpp.domain.assembly('umamusume').image;
    const sd = Il2Cpp.gc.choose(img.class('Gallop.RaceSimulateData'))[0];
    if (!sd) { send({type:'fatal', err:'no live RaceSimulateData - load a replay'}); return; }
    const out = {horseNum: sd.field('_horseNum').value};
    const fl = sd.field('_frameDataList').value;
    const size = fl.field('_size').value;
    const items = fl.field('_items').value;
    out.frameCount = size;

    // Field list of the per-horse frame record.
    const f0 = items.get(0);
    const hda0 = f0.field('HorseDataArray').value;
    const h0 = hda0.get(0);
    out.horseFrameFields = [];
    h0.class.fields.filter(f=>!f.isStatic&&!f.isLiteral).forEach(f=>{
      let tn='?'; try{tn=f.type.name;}catch(e){}
      out.horseFrameFields.push({name:f.name, type:tn});
    });

    // Walk every frame, pulling runner 0's values + frame time.
    const names = out.horseFrameFields.map(f=>f.name);
    out.track = [];
    for (let i=0;i<size;i++) {
      const fr = items.get(i);
      const t = fr.field('Time').value;
      const hda = fr.field('HorseDataArray').value;
      const row = {t: t, h: []};
      for (let k=0;k<Math.min(hda.length, 3);k++) {   // first 3 runners
        const h = hda.get(k);
        const rec = {};
        names.forEach(n=>{
          try { const v = h.field(n).value;
            rec[n] = (typeof v==='number'||typeof v==='boolean') ? v :
                     (v && v.length!==undefined) ? ('<len '+v.length+'>') : null;
          } catch(e){ rec[n]=null; }
        });
        row.h.push(rec);
      }
      out.track.push(row);
    }
    send({type:'frames', data: out});
    send({type:'done'});
  } catch(e){ send({type:'fatal', err:e.message, stack:e.stack}); }
}); }, 500);
"""
import frida
pid = next((p.pid for p in frida.get_local_device().enumerate_processes()
            if p.name.lower()=="umamusumeprettyderby.exe"), None)
print(f"[+] attaching {pid}")
st={"f":False,"d":None}
def om(m,d):
    if m["type"]=="send":
        p=m["payload"]
        if p.get("type")=="frames": st["d"]=p["data"]
        elif p.get("type")=="done": st["f"]=True
        elif p.get("type")=="fatal": print("[X]",p.get("err")); st["f"]=True
    elif m["type"]=="error": print("[X]",m.get("description")); st["f"]=True
s=frida.attach(pid); sc=s.create_script(BRIDGE_JS.read_text(encoding="utf-8")+"\n"+AGENT)
sc.on("message",om); sc.load()
dl=time.time()+120
while time.time()<dl and not st["f"]: time.sleep(0.1)
s.detach()
if st["d"]:
    Path(BASE_DIR/"frames.json").write_text(json.dumps(st["d"]))
    print(f"[+] wrote frames.json ({st['d']['frameCount']} frames)")
