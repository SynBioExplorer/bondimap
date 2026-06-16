#!/usr/bin/env python3
"""Interactive landmark placement tool.

    python make_placer.py [output/bondi_r0c0.3mf] [config.json]

Writes output/placer.html: the map plus the landmark models with translate /
rotate / scale gizmos and a live panel that prints each landmark's
lat / lon / size_mm / rotation_deg as a config-ready snippet. Position them in
the browser, copy the JSON, and paste it back to bake into config.json.
"""

import base64
import json
import math
import sys
from pathlib import Path

import trimesh

from bondimap import config, landmarks

OPERA_BASE = 25.0     # mm the opera GLB is built at (scale 1 in the tool = this)
BRIDGE_BASE = 50.0
TERRAIN_TARGET = 300_000


def _decimate(m, n):
    if len(m.faces) <= n:
        return m
    try:
        return m.simplify_quadric_decimation(face_count=n)
    except Exception:
        return m


def _map_glb(src):
    scene = trimesh.load(src, process=False)
    geoms = {}
    targets = {"terrain": 120_000, "buildings": 220_000}   # placement reference; keep light
    for name, m in scene.geometry.items():
        key = name.lower()
        if any(s in key for s in ("streets", "trees", "landmarks")):
            continue
        for t, n in targets.items():
            if t in key:
                n0 = len(m.faces)
                m = _decimate(m, n)
                print(f"  {name}: {n0:,} -> {len(m.faces):,} faces")
        geoms[name] = m
    return trimesh.Scene(geoms).export(file_type="glb")


def _model_xy(cfg, lon, lat):
    ux, uy = cfg.to_utm.transform(lon, lat)
    mx, my = cfg.utm_to_model(ux, uy)
    return float(mx), float(my)


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/bondi_r0c0.3mf")
    cfg_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("config.json")
    cfg = config.load(cfg_path)

    # Reuse the already-decimated map GLB embedded in the viewer HTML (fast, no
    # 67 MB 3MF reload); fall back to building it from the 3MF if absent.
    import re
    viewer = src.with_suffix(".html")
    map_b64 = None
    if viewer.exists():
        mm = re.search(r'GLB_B64\s*=\s*"([A-Za-z0-9+/=]+)"', viewer.read_text())
        if mm:
            map_b64 = mm.group(1)
            print(f"reusing map glb from {viewer.name}")
    if map_b64 is None:
        print("building map glb from 3mf ...")
        map_b64 = base64.b64encode(_map_glb(src)).decode()
    opera_b64 = base64.b64encode(
        trimesh.Scene({"opera": landmarks.opera_house(OPERA_BASE)}).export(file_type="glb")).decode()
    bridge_b64 = base64.b64encode(
        trimesh.Scene({"bridge": landmarks._from_file(cfg.path.parent / "habourbridge.stl",
                                                      BRIDGE_BASE, "z")}).export(file_type="glb")).decode()

    # initial transforms from the (currently disabled) landmarks block, if present
    items = {it.get("file", it["type"]): it for it in cfg.raw.get("landmarks", {}).get("items", [])}
    ox = oy = 250.0
    o_rot = -30.0
    b_x = b_y = 230.0
    b_rot = 0.0
    for it in cfg.raw.get("landmarks", {}).get("items", []):
        if it["type"] == "opera_house":
            ox, oy = _model_xy(cfg, it["lon"], it["lat"]); o_rot = it.get("rotation_deg", -30)
        elif "end_a" in it:
            ax, ay = _model_xy(cfg, it["end_a"][1], it["end_a"][0])
            bx, by = _model_xy(cfg, it["end_b"][1], it["end_b"][0])
            b_x, b_y = (ax + bx) / 2, (ay + by) / 2
            b_rot = math.degrees(math.atan2(by - ay, bx - ax)) + it.get("rotation_deg", 0)

    oxm, oym = cfg.origin_utm
    subs = {
        "__MAP__": map_b64, "__OPERA__": opera_b64, "__BRIDGE__": bridge_b64,
        "__PROJ__": cfg.utm_crs.to_proj4(),
        "__OX__": repr(oxm), "__OY__": repr(oym), "__SCALE__": repr(cfg.scale),
        "__OPERA_BASE__": repr(OPERA_BASE), "__BRIDGE_BASE__": repr(BRIDGE_BASE),
        "__O_INIT__": json.dumps({"x": ox, "y": oy, "rot": o_rot}),
        "__B_INIT__": json.dumps({"x": b_x, "y": b_y, "rot": b_rot}),
    }
    html = _TEMPLATE
    for k, v in subs.items():
        html = html.replace(k, v)
    out = src.parent / "placer.html"
    out.write_text(html)
    print(f"wrote {out}  ({len(html)/1e6:.1f} MB)")


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Landmark placer</title>
<style>
  html,body{margin:0;height:100%;background:#3a3f47;overflow:hidden;font-family:system-ui,sans-serif;color:#eee}
  #c{display:block;width:100%;height:100%}
  #panel{position:fixed;top:10px;left:10px;background:#0009;border:1px solid #fff3;border-radius:8px;padding:10px 12px;font-size:12px;max-width:360px}
  #panel b{color:#9ad}
  button{background:#2b6;border:0;color:#fff;border-radius:5px;padding:5px 9px;margin:2px;cursor:pointer;font-size:12px}
  button.sel{background:#28d}
  #out{width:340px;height:150px;background:#111;color:#7e7;border:1px solid #fff3;border-radius:5px;font:11px monospace;margin-top:6px}
  #hint{position:fixed;bottom:10px;left:10px;font-size:12px;opacity:.7;text-shadow:0 1px 2px #000}
  #load{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#3a3f47;font-size:15px}
</style></head>
<body>
<canvas id="c"></canvas>
<div id="load">Loading…</div>
<div id="panel">
  <div><b>Select:</b>
    <button id="selOpera" class="sel">Opera House</button>
    <button id="selBridge">Harbour Bridge</button></div>
  <div><b>Mode:</b>
    <button id="mMove" class="sel">Move (W)</button>
    <button id="mRot">Rotate (E)</button>
    <button id="mScale">Scale (R)</button></div>
  <div style="margin-top:6px"><b>config landmarks → items:</b>
    <button id="copy" style="background:#a63">Copy JSON</button></div>
  <textarea id="out" readonly></textarea>
  <div id="status" style="margin-top:4px;opacity:.8"></div>
</div>
<div id="hint">drag the gizmo to place • orbit with empty-space drag • scroll to zoom • click a landmark to select</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/proj4js/2.11.0/proj4.js"></script>
<script type="importmap">
{ "imports": {
  "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
  "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const PROJ="__PROJ__", OX=__OX__, OY=__OY__, SCALE=__SCALE__;
const OPERA_BASE=__OPERA_BASE__, BRIDGE_BASE=__BRIDGE_BASE__;
const O_INIT=__O_INIT__, B_INIT=__B_INIT__;
const toLL = (x,y) => { const [lon,lat]=proj4(PROJ,"EPSG:4326",[OX + x/SCALE, OY + y/SCALE]); return {lon,lat}; };

const renderer=new THREE.WebGLRenderer({canvas:c,antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.setSize(innerWidth,innerHeight);
const scene=new THREE.Scene(); scene.background=new THREE.Color(0x3a3f47);
const camera=new THREE.PerspectiveCamera(45,innerWidth/innerHeight,0.1,100000);
camera.up.set(0,0,1);                                   // Z-up: world == model coords
const orbit=new OrbitControls(camera,renderer.domElement); orbit.enableDamping=true;
scene.add(new THREE.HemisphereLight(0xffffff,0x445,0.6));
const key=new THREE.DirectionalLight(0xffffff,2.4); key.position.set(-1,-0.8,1.4); scene.add(key);

const gizmo=new TransformControls(camera,renderer.domElement);
gizmo.addEventListener('dragging-changed',e=>orbit.enabled=!e.value);
gizmo.addEventListener('objectChange',onChange);
scene.add(gizmo);

const loader=new GLTFLoader();
let terrain=null, opera=null, bridge=null, selected=null;
const buf=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0)).buffer;

loader.parse(buf("__MAP__"),'',(g)=>{
  g.scene.traverse(o=>{ if(o.isMesh){ o.geometry.computeVertexNormals();
    const w=o.name.toLowerCase().includes('water');
    o.material=new THREE.MeshStandardMaterial({color:w?0x111418:0xffffff,roughness:0.95,side:THREE.DoubleSide});
    if(o.name.toLowerCase().includes('terrain')) terrain=o; }});
  scene.add(g.scene);
  const b=new THREE.Box3().setFromObject(g.scene), c2=b.getCenter(new THREE.Vector3());
  orbit.target.copy(c2); camera.position.set(c2.x-120, c2.y-200, 180); orbit.update();
  loadLandmarks();
  document.getElementById('load').remove();
});

function tint(obj,hex){ obj.traverse(o=>{ if(o.isMesh){ o.geometry.computeVertexNormals();
  o.material=new THREE.MeshStandardMaterial({color:hex,roughness:0.5,emissive:hex,emissiveIntensity:0.25,side:THREE.DoubleSide}); } }); }
function surfaceZ(x,y){ if(!terrain) return 3; const rc=new THREE.Raycaster(); rc.set(new THREE.Vector3(x,y,400),new THREE.Vector3(0,0,-1));
  const h=rc.intersectObject(terrain,true); return h.length? h[0].point.z : 3; }

function place(obj,init){ obj.position.set(init.x, init.y, surfaceZ(init.x,init.y)); obj.rotation.z=THREE.MathUtils.degToRad(init.rot); }

function loadLandmarks(){
  loader.parse(buf("__OPERA__"),'',(g)=>{ opera=g.scene; tint(opera,0xffcc44); place(opera,O_INIT); scene.add(opera);
    loader.parse(buf("__BRIDGE__"),'',(g2)=>{ bridge=g2.scene; tint(bridge,0x55ccff); place(bridge,B_INIT); scene.add(bridge);
      select(opera); onChange(); }); });
}

function select(obj){ selected=obj; gizmo.attach(obj);
  document.getElementById('selOpera').className = obj===opera?'sel':'';
  document.getElementById('selBridge').className = obj===bridge?'sel':''; }

function onChange(){
  if(gizmo.mode==='scale'){ const s=selected.scale, u=(s.x+s.y+s.z)/3; s.set(u,u,u); }   // uniform
  if(gizmo.mode==='translate' && selected) selected.position.z=surfaceZ(selected.position.x,selected.position.y);
  updateOut();
}

function snippet(obj, head){
  const ll=toLL(obj.position.x,obj.position.y);
  const size = (obj===opera?OPERA_BASE:BRIDGE_BASE)*obj.scale.x;
  let rot = (THREE.MathUtils.radToDeg(obj.rotation.z))%360; if(rot>180)rot-=360; if(rot<-180)rot+=360;
  return Object.assign(head, {lat:+ll.lat.toFixed(5), lon:+ll.lon.toFixed(5),
                              size_mm:+size.toFixed(1), rotation_deg:+rot.toFixed(1)});
}
function updateOut(){
  if(!opera||!bridge) return;
  const items=[ snippet(opera,{type:"opera_house"}),
                snippet(bridge,{type:"file",file:"habourbridge.stl",up:"z"}) ];
  document.getElementById('out').value = JSON.stringify(items,null,2);
}

// UI
const setMode=m=>{ gizmo.setMode(m); gizmo.showZ=(m!=='translate'); gizmo.showX=gizmo.showY=(m!=='rotate');
  if(m==='rotate'){gizmo.showZ=true;gizmo.showX=gizmo.showY=false;}
  for(const[id,mm] of [['mMove','translate'],['mRot','rotate'],['mScale','scale']])
    document.getElementById(id).className = m===mm?'sel':''; };
document.getElementById('mMove').onclick=()=>setMode('translate');
document.getElementById('mRot').onclick=()=>setMode('rotate');
document.getElementById('mScale').onclick=()=>setMode('scale');
document.getElementById('selOpera').onclick=()=>select(opera);
document.getElementById('selBridge').onclick=()=>select(bridge);
document.getElementById('copy').onclick=()=>{ navigator.clipboard.writeText(document.getElementById('out').value);
  document.getElementById('status').textContent='copied to clipboard ✓'; };
addEventListener('keydown',e=>{ if(e.key==='w')setMode('translate'); if(e.key==='e')setMode('rotate'); if(e.key==='r')setMode('scale'); });
setMode('translate');

// click to select a landmark
const ray=new THREE.Raycaster();
renderer.domElement.addEventListener('pointerdown',e=>{
  if(gizmo.dragging) return;
  const m=new THREE.Vector2((e.clientX/innerWidth)*2-1, -(e.clientY/innerHeight)*2+1);
  ray.setFromCamera(m,camera);
  for(const[obj] of [[opera],[bridge]]){ if(obj && ray.intersectObject(obj,true).length){ select(obj); return; } }
});

addEventListener('resize',()=>{ camera.aspect=innerWidth/innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth,innerHeight); });
(function loop(){ requestAnimationFrame(loop); orbit.update(); renderer.render(scene,camera); })();
</script>
</body></html>"""


if __name__ == "__main__":
    main()
