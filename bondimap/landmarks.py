"""Procedural, print-ready stylized landmark models, placed by lat/lon.

Auto-download of real STLs is blocked (every host gates it behind a login), and
at map scale a high-poly replica's detail is sub-nozzle anyway, so these are
clean parametric icons sized to be recognisable on the finished piece. Each is
built centred on the origin, long axis +X, Z up, resting on z=0, then scaled and
placed at the landmark's real coordinates.
"""

from __future__ import annotations

import math

import numpy as np
import trimesh
from shapely.geometry import Polygon

T = trimesh.transformations


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _bezier(p0, p1, p2, n):
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2 = map(np.asarray, (p0, p1, p2))
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2


def _tube(p0, p1, r):
    return trimesh.creation.cylinder(radius=r, segment=[p0, p1], sections=10)


# --------------------------------------------------------------------------- #
# Opera House: a podium under clusters of curved "sail" shells
# --------------------------------------------------------------------------- #
def _sail(height, radius):
    """A curved shell: the front half of a tall cone (flat back at y=0, curved
    bulge toward +y, pointed top). Reads as a billowing Opera House sail; rows
    of them overlap into the iconic shells."""
    c = trimesh.creation.cone(radius=radius, height=height, sections=28)
    c = trimesh.intersections.slice_mesh_plane(
        c, plane_normal=[0, 1, 0], plane_origin=[0, 0, 0], cap=True)
    c.apply_scale([0.78, 1.0, 1.0])   # narrow it a touch -> less conical, more sail
    return c


def opera_house(size_mm):
    s = size_mm                       # building length (x)
    parts = []

    podium = trimesh.creation.box(extents=[0.95 * s, 0.50 * s, 0.08 * s])
    podium.apply_translation([0, 0, 0.04 * s])
    parts.append(podium)

    top = 0.08 * s
    # Curved shells with their flat backs at y=0 bulging toward +y, set in a row
    # along x and overlapping. Each cluster rises then falls (like the real
    # vaults); a slight backward lean tips the points over the shells behind.
    clusters = [(-0.34 * s, [0.30, 0.46, 0.38, 0.24]),   # main vault
                (0.04 * s, [0.26, 0.40, 0.32, 0.20])]     # second vault
    for cx, heights in clusters:
        x = cx
        for h in heights:
            sail = _sail(h * s, 0.20 * s)
            sail.apply_transform(T.rotation_matrix(math.radians(-8), [1, 0, 0]))  # lean back
            sail.apply_translation([x, -0.04 * s, top])
            parts.append(sail)
            x += 0.13 * s
    # small restaurant shell set apart
    sm = _sail(0.16 * s, 0.15 * s)
    sm.apply_transform(T.rotation_matrix(math.radians(-8), [1, 0, 0]))
    sm.apply_translation([0.44 * s, -0.10 * s, top])
    parts.append(sm)

    house = trimesh.util.concatenate(parts)
    house.apply_translation([0, 0, -house.bounds[0][2]])   # rest on z=0
    return house


# --------------------------------------------------------------------------- #
# Harbour Bridge: steel through-arch + deck + pylons + hangers
# --------------------------------------------------------------------------- #
def harbour_bridge(length_mm):
    L = length_mm
    arch_h = 0.26 * L
    deck_z = 0.11 * L
    r = 0.016 * L
    half_w = 0.12 * L
    span = 0.46 * L
    parts = []

    def arch_z(x):
        return deck_z + arch_h * max(0.0, 1.0 - (x / span) ** 2)

    xs = np.linspace(-L / 2, L / 2, 26)
    for side in (-half_w, half_w):
        pts = [(float(x), side, arch_z(x)) for x in xs]
        for a, b in zip(pts, pts[1:]):
            parts.append(_tube(a, b, r))
    # cross-braces over the crown
    for x in np.linspace(-0.3 * L, 0.3 * L, 5):
        parts.append(_tube((x, -half_w, arch_z(x)), (x, half_w, arch_z(x)), r * 0.5))
    # deck
    deck = trimesh.creation.box(extents=[0.95 * L, 2.1 * half_w, 0.16 * deck_z * 6])
    deck.apply_translation([0, 0, deck_z])
    parts.append(deck)
    # hangers
    for x in np.linspace(-0.4 * L, 0.4 * L, 11):
        for side in (-half_w, half_w):
            top = arch_z(x)
            if top > deck_z + r:
                parts.append(_tube((x, side, deck_z), (x, side, top), r * 0.32))
    # pylons (two per end)
    for sx in (-1, 1):
        for side in (-half_w, half_w):
            py = trimesh.creation.box(extents=[0.045 * L, 0.55 * half_w, 2.0 * deck_z])
            py.apply_translation([sx * 0.43 * L, side, deck_z])
            parts.append(py)

    bridge = trimesh.util.concatenate(parts)
    bridge.apply_translation([0, 0, -bridge.bounds[0][2]])   # rest on z=0
    return bridge


_GENERATORS = {"opera_house": opera_house, "harbour_bridge": harbour_bridge}


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
def _from_file(path, size_mm, up="z"):
    """Load an STL/3MF, orient Z-up, scale to size_mm (max horizontal extent),
    centre on XY and rest the base on z=0."""
    o = trimesh.load(str(path), process=False)
    m = o.to_geometry() if isinstance(o, trimesh.Scene) else o
    m = m.copy()
    if up == "y":
        m.apply_transform(T.rotation_matrix(math.pi / 2, [1, 0, 0]))
    elif up == "x":
        m.apply_transform(T.rotation_matrix(-math.pi / 2, [0, 1, 0]))
    # standardise so the longer horizontal axis is +X (matches placement/endpoints)
    if m.extents[1] > m.extents[0]:
        m.apply_transform(T.rotation_matrix(math.pi / 2, [0, 0, 1]))
    horiz = max(m.extents[0], m.extents[1])
    if horiz > 0:
        m.apply_scale(size_mm / horiz)
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2.0, -(lo[1] + hi[1]) / 2.0, -lo[2]])
    return m


def _model_xy(cfg, lon, lat):
    ux, uy = cfg.to_utm.transform(lon, lat)
    mx, my = cfg.utm_to_model(ux, uy)
    return float(mx), float(my)


def build_landmarks(cfg, sampler, color):
    lcfg = cfg.raw.get("landmarks", {})
    if not lcfg.get("enabled"):
        return None
    water_level = float(cfg["features"]["water"]["level_mm"])

    meshes = []
    for item in lcfg.get("items", []):
        kind = item["type"]
        if kind == "file":
            mesh = _from_file(cfg.path.parent / item["file"],
                              float(item["size_mm"]), item.get("up", "z"))
        elif kind in _GENERATORS:
            mesh = _GENERATORS[kind](float(item["size_mm"]))
        else:
            print(f"Landmark  : unknown type '{kind}', skipped")
            continue

        if "end_a" in item and "end_b" in item:           # span between two points
            ax, ay = _model_xy(cfg, item["end_a"][1], item["end_a"][0])
            bx, by = _model_xy(cfg, item["end_b"][1], item["end_b"][0])
            mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
            angle = math.atan2(by - ay, bx - ax) + math.radians(float(item.get("rotation_deg", 0.0)))
            base_z = float(item.get("base_z_mm", water_level))
        else:                                              # point landmark
            mx, my = _model_xy(cfg, item["lon"], item["lat"])
            angle = math.radians(float(item.get("rotation_deg", 0.0)))
            ground = float(sampler(mx, my)[0])
            base_z = float(item.get("base_z_mm", ground)) - 0.4   # sink to fuse

        mesh.apply_transform(T.rotation_matrix(angle, [0, 0, 1]))
        mesh.apply_translation([mx, my, base_z])
        meshes.append(mesh)
        print(f"Landmark  : {kind} at model ({mx:.0f}, {my:.0f}) mm, base z={base_z:.1f}")

    if not meshes:
        return None
    out = trimesh.util.concatenate(meshes)
    out.visual.face_colors = color
    return out
