"""Dump the game's parsed RaceSimulateData: horse results, frames, events."""
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
    if (!sd) { send({type:'fatal', err:'no RaceSimulateData live'}); return; }
    const out = {horseNum: sd.field('_horseNum').value,
                 lastFrameIdx: sd.field('_lastFrameIdx').value,
                 lastCalcFrameTime: sd.field('_lastCalcFrameTime').value};

    function scal(o, names) {
      const r = {};
      names.forEach(n => { try { const v = o.field(n).value;
        r[n] = (v && v.content !== undefined) ? v.content : v; } catch(e){ r[n]=null; } });
      return r;
    }

    // horse results
    const hr = sd.field('_horseResultDataArray').value;
    out.horses = [];
    for (let i=0;i<hr.length;i++) {
      out.horses.push(scal(hr.get(i), ['FinishOrder','FinishTime','FinishTimeRaw',
        'FinishDiffTime','StartDelayTime','GutsOrder','WizOrder',
        'LastSpurtStartDistance','RunningStyle','Defeat']));
    }

    // frame list -> how big, and one sample frame's shape
    const fl = sd.field('_frameDataList').value;
    const fsize = fl.field('_size').value;
    out.frameCount = fsize;
    const fitems = fl.field('_items').value;
    const f0 = fitems.get(0);
    out.frameFields = [];
    f0.class.fields.filter(f=>!f.isStatic&&!f.isLiteral).forEach(f=>{
      let tn='?'; try{tn=f.type.name;}catch(e){}
      let val=null;
      try { const v=f0.field(f.name).value;
        val = (v===null||v===undefined)?null:
              (typeof v==='number'||typeof v==='boolean')?v:
              (v.length!==undefined)?('<len '+v.length+'>'):
              (v.class&&v.class.type)?('<'+v.class.type.name+'>'):'<obj>'; } catch(e){}
      out.frameFields.push({name:f.name, type:tn, sample:val});
    });

    // events
    const el = sd.field('_simEvDataList').value;
    out.eventCount = el.field('_size').value;
    const eitems = el.field('_items').value;
    out.eventFields = [];
    if (out.eventCount > 0) {
      const e0 = eitems.get(0);
      e0.class.fields.filter(f=>!f.isStatic&&!f.isLiteral).forEach(f=>{
        let tn='?'; try{tn=f.type.name;}catch(e){}
        let val=null;
        try { const v=e0.field(f.name).value;
          val = (v===null||v===undefined)?null:
                (typeof v==='number')?v:
                (v.length!==undefined)?('<len '+v.length+'>'):'<obj>'; } catch(e){}
        out.eventFields.push({name:f.name, type:tn, sample:val});
      });
    }
    send({type:'simdata', data: out});
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
        if p.get("type")=="simdata": st["d"]=p["data"]
        elif p.get("type")=="done": st["f"]=True
        elif p.get("type")=="fatal": print("[X]",p.get("err")); st["f"]=True
    elif m["type"]=="error": print("[X]",m.get("description")); st["f"]=True
s=frida.attach(pid); sc=s.create_script(BRIDGE_JS.read_text(encoding="utf-8")+"\n"+AGENT)
sc.on("message",om); sc.load()
dl=time.time()+90
while time.time()<dl and not st["f"]: time.sleep(0.1)
s.detach()
if st["d"]:
    Path(BASE_DIR/"simdata.json").write_text(json.dumps(st["d"], indent=2))
    print("[+] wrote simdata.json")
