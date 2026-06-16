"""Write a multi-object, multi-colour .3mf.

One <object> per category, each tied to a <base> material whose displaycolor is
the category colour. Bambu Studio / OrcaSlicer import these as separate coloured
objects; assign an AMS filament to each. Pure-stdlib (zipfile + XML strings) so
colours are guaranteed to survive, unlike trimesh's generic 3MF export.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodelrelationship"/>
</Relationships>"""


def _hex_to_3mf(color: str) -> str:
    c = color.lstrip("#")
    if len(c) == 6:
        c += "FF"
    return "#" + c.upper()


def _mesh_xml(mesh) -> str:
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)
    verts = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in v)
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f)
    return f"<mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh>"


def write_3mf(path, named_meshes: dict, colors: dict):
    """named_meshes: {category: trimesh}. colors: {category: '#RRGGBB'}."""
    path = Path(path)
    items = [(name, m) for name, m in named_meshes.items() if m is not None and len(m.faces)]
    if not items:
        print(f"  (empty, skipped {path.name})")
        return

    bases = "".join(
        f'<base name="{name}" displaycolor="{_hex_to_3mf(colors.get(name, "#CCCCCC"))}"/>'
        for name, _ in items
    )
    objects, build = [], []
    for i, (name, mesh) in enumerate(items):
        oid = i + 2  # 1 is the basematerials resource
        objects.append(
            f'<object id="{oid}" name="{name}" type="model" pid="1" pindex="{i}">'
            f'{_mesh_xml(mesh)}</object>'
        )
        build.append(f'<item objectid="{oid}"/>')

    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">'
        f'<resources><basematerials id="1">{bases}</basematerials>'
        f'{"".join(objects)}</resources>'
        f'<build>{"".join(build)}</build></model>'
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model)

    tris = sum(len(m.faces) for _, m in items)
    print(f"  wrote {path.name}  ({len(items)} objects, {tris:,} triangles)")
