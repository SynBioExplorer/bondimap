#!/usr/bin/env python3
"""Validate a generated .3mf: structure, colours, and that it re-loads as meshes.

Usage: python verify_3mf.py output/test/test_r0c0.3mf
"""

import re
import sys
import zipfile

import trimesh


def main(path):
    print(f"== {path} ==")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert "3D/3dmodel.model" in names, "missing model part"
        assert "[Content_Types].xml" in names and "_rels/.rels" in names, "missing OPC parts"
        model = z.read("3D/3dmodel.model").decode()

    bases = re.findall(r'<base name="([^"]+)" displaycolor="([^"]+)"', model)
    objects = re.findall(r'<object id="\d+" name="([^"]+)"', model)
    print(f"materials : {bases}")
    print(f"objects   : {objects}")
    assert bases, "no base materials (colours) written"
    assert len(objects) == len(bases), "object/material count mismatch"

    # Strict OPC read with lib3mf (the reference reader slicers are tested
    # against). trimesh is lenient and will load a malformed package that
    # OrcaSlicer/Bambu Studio reject, so this is the authoritative check.
    import lib3mf
    w = lib3mf.get_wrapper()
    lm = w.CreateModel()
    lm.QueryReader("3mf").ReadFromFile(str(path))
    it = lm.GetMeshObjects()
    lib3mf_objs = []
    while it.MoveNext():
        o = it.GetCurrentMeshObject()
        lib3mf_objs.append(o.GetName())
    assert lib3mf_objs, "lib3mf found no mesh objects (OPC/structure invalid)"
    print(f"lib3mf    : strict read OK -> {lib3mf_objs}")

    scene = trimesh.load(path, process=False)
    geoms = scene.geometry if hasattr(scene, "geometry") else {"_": scene}
    print(f"reloaded  : {len(geoms)} geometr(ies)")
    total = 0
    for name, g in geoms.items():
        total += len(g.faces)
        print(f"  - {name:>10}: {len(g.vertices):>7} verts  {len(g.faces):>7} faces  "
              f"watertight={g.is_watertight}")
    lo, hi = scene.bounds
    print(f"bounds mm : x[{lo[0]:.1f},{hi[0]:.1f}] y[{lo[1]:.1f},{hi[1]:.1f}] z[{lo[2]:.1f},{hi[2]:.1f}]")
    print(f"total     : {total:,} triangles")
    print("OK")


if __name__ == "__main__":
    main(sys.argv[1])
