#!/usr/bin/env python3
"""Build a self-contained interactive 3D HTML viewer from a generated .3mf.

    python make_viewer.py [output/bondi_r0c0.3mf] [config.json]

Loads the meshes, decimates the (smooth) terrain so the file stays reasonable,
exports a binary GLB, and embeds it as base64 in a single HTML file with a
Three.js orbit viewer. Colours are assigned per object name in the browser, so
the viewer always matches the `colors` block in config.json. Self-contained:
double-click the .html (Three.js loads from a CDN; the model is embedded, so no
local-file fetch is needed).
"""

import base64
import json
import sys
from pathlib import Path

import trimesh

TERRAIN_TARGET_FACES = 300_000
# Flat ground-cover layers that are invisible in the default white-on-white
# scheme; omitted from the viewer to shrink the file. They remain in the .3mf.
SKIP_LAYERS = {"streets", "trees"}


def _decimate(mesh, target):
    if len(mesh.faces) <= target:
        return mesh
    try:
        return mesh.simplify_quadric_decimation(face_count=target)
    except Exception as exc:
        print(f"  (decimation unavailable: {exc}; keeping full terrain)")
        return mesh


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/bondi_r0c0.3mf")
    cfg_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("config.json")
    colors = {k: v for k, v in json.loads(cfg_path.read_text())["colors"].items()
              if not k.startswith("_")}

    print(f"Loading {src} ...")
    scene = trimesh.load(src, process=False)
    geoms = {}
    for name, m in scene.geometry.items():
        key = name.lower()
        if any(s in key for s in SKIP_LAYERS):
            print(f"  skip {name} ({len(m.faces):,} faces, invisible in viewer)")
            continue
        if "terrain" in key and len(m.faces) > TERRAIN_TARGET_FACES:
            n0 = len(m.faces)
            m = _decimate(m, TERRAIN_TARGET_FACES)
            print(f"  terrain {n0:,} -> {len(m.faces):,} faces")
        geoms[name] = m

    glb = trimesh.Scene(geoms).export(file_type="glb")
    print(f"GLB: {len(glb)/1e6:.1f} MB")

    html = _TEMPLATE
    html = html.replace("__TITLE__", src.stem)
    html = html.replace("__COLORS__", json.dumps(colors))
    html = html.replace("__GLB_B64__", base64.b64encode(glb).decode())
    out = src.with_suffix(".html")
    out.write_text(html)
    print(f"wrote {out}  ({len(html)/1e6:.1f} MB)")


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__ - 3D map</title>
<style>
  html,body{margin:0;height:100%;background:#3a3a3a;overflow:hidden;font-family:system-ui,sans-serif}
  #c{display:block;width:100%;height:100%}
  #hud{position:fixed;left:14px;bottom:12px;color:#eee;font-size:12px;opacity:.7;
       text-shadow:0 1px 2px #000;pointer-events:none}
  #spin{position:fixed;right:14px;top:12px;color:#eee;font-size:12px;background:#0006;
        border:1px solid #fff3;border-radius:6px;padding:6px 10px;cursor:pointer}
  #load{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
        color:#ddd;font-size:15px;background:#3a3a3a}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="load">Loading model...</div>
<div id="spin">auto-rotate: on</div>
<div id="hud">drag to orbit &middot; scroll to zoom &middot; right-drag to pan</div>
<script type="importmap">
{ "imports": {
  "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
  "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const COLORS = __COLORS__;
const GLB_B64 = "__GLB_B64__";

const renderer = new THREE.WebGLRenderer({canvas: document.getElementById('c'), antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x3a3a3a);

const camera = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 0.1, 100000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.6;

scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 2.4);
key.position.set(-1, 1.4, 0.8);              // NW, high: classic hillshade angle
scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.5);
fill.position.set(1, 0.6, -0.8);
scene.add(fill);

function colorFor(name){
  name = (name||'').toLowerCase();
  for (const k in COLORS) if (name.includes(k)) return new THREE.Color(COLORS[k]);
  return new THREE.Color(0xcccccc);
}

const buf = Uint8Array.from(atob(GLB_B64), c => c.charCodeAt(0));
new GLTFLoader().parse(buf.buffer, '', (gltf) => {
  const model = gltf.scene;
  model.traverse(o => {
    if (!o.isMesh) return;
    o.geometry.computeVertexNormals();
    o.material = new THREE.MeshStandardMaterial({
      color: colorFor(o.name), roughness: 0.9, metalness: 0.0,
      side: THREE.DoubleSide,   // meshes aren't consistently wound; don't cull
      flatShading: o.name.toLowerCase().includes('terrain')
    });
  });
  model.rotation.x = -Math.PI/2;             // model is Z-up; Three.js is Y-up
  model.updateMatrixWorld(true);

  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  model.position.sub(center);                // recenter at origin
  scene.add(model);

  const span = Math.max(size.x, size.z);
  camera.position.set(span*0.05, span*0.75, span*0.95);
  camera.near = span/1000; camera.far = span*20; camera.updateProjectionMatrix();
  controls.target.set(0,0,0); controls.update();
  document.getElementById('load').remove();
}, (err) => {
  document.getElementById('load').textContent = 'Failed to load model: ' + err;
});

const spin = document.getElementById('spin');
spin.onclick = () => { controls.autoRotate = !controls.autoRotate;
  spin.textContent = 'auto-rotate: ' + (controls.autoRotate ? 'on' : 'off'); };

addEventListener('resize', () => {
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
