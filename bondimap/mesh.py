"""Mesh construction. Everything is built in model millimetres.

Strategy for multi-colour FDM: the terrain is one grey relief slab; water,
trees and streets are thin coloured slabs that hug (or, for water, flatten over)
the terrain; buildings are coloured extrusions. Each category becomes one
trimesh, and each trimesh becomes one coloured object in the .3mf.
"""

from __future__ import annotations

import math

import mapbox_earcut as earcut
import numpy as np
import trimesh
from shapely.geometry import box

from .elevation import TerrainSampler


# --------------------------------------------------------------------------- #
# Low-level primitives
# --------------------------------------------------------------------------- #
def _rings(poly):
    """Exterior + interior rings as (verts2d, ring_end_indices) for earcut."""
    ext = np.asarray(poly.exterior.coords[:-1], dtype=np.float64)
    parts = [ext]
    ends = [len(ext)]
    for interior in poly.interiors:
        h = np.asarray(interior.coords[:-1], dtype=np.float64)
        parts.append(h)
        ends.append(ends[-1] + len(h))
    return np.vstack(parts), np.asarray(ends, dtype=np.uint32)


def _prism(poly, z_bottom, z_top):
    """Solid between two z-surfaces over a polygon.

    z_bottom / z_top may be scalars (flat) or arrays of length len(verts2d)
    (per-vertex, for draping over terrain). Returns (vertices, faces) or None.
    """
    if poly.is_empty or poly.area <= 0 or poly.geom_type != "Polygon":
        return None
    verts2d, ends = _rings(poly)
    if len(verts2d) < 3:
        return None
    try:
        tri = earcut.triangulate_float64(np.ascontiguousarray(verts2d), ends)
    except Exception:
        return None
    if len(tri) == 0:
        return None
    tri = tri.reshape(-1, 3).astype(np.int64)

    m = len(verts2d)
    zb = np.full(m, z_bottom) if np.isscalar(z_bottom) else np.asarray(z_bottom)
    zt = np.full(m, z_top) if np.isscalar(z_top) else np.asarray(z_top)
    top = np.column_stack([verts2d, zt])
    bot = np.column_stack([verts2d, zb])
    verts = np.vstack([top, bot])

    faces = [tri, tri[:, ::-1] + m]  # top up, bottom reversed
    start = 0
    for end in ends:
        idx = np.arange(start, end)
        nxt = np.roll(idx, -1)
        a, b = idx, nxt
        faces.append(np.column_stack([a, b, b + m]))
        faces.append(np.column_stack([a, b + m, a + m]))
        start = end
    return verts, np.vstack(faces)


def _combine(pieces, color):
    """Concatenate (verts, faces) pieces into one coloured trimesh."""
    pieces = [p for p in pieces if p is not None]
    if not pieces:
        return None
    all_v, all_f, off = [], [], 0
    for v, f in pieces:
        all_v.append(v)
        all_f.append(f + off)
        off += len(v)
    mesh = trimesh.Trimesh(vertices=np.vstack(all_v), faces=np.vstack(all_f),
                           process=False)
    mesh.visual.face_colors = color
    return mesh


# --------------------------------------------------------------------------- #
# Terrain
# --------------------------------------------------------------------------- #
def terrain_tile(cfg, z_mm, x0, x1, y0, y1, color):
    """Closed grey slab for the grid region covering model bounds [x0,x1]x[y0,y1]."""
    N = z_mm.shape[0]
    size = cfg.size_mm
    i0 = max(0, int(np.floor(x0 / size * (N - 1))))
    i1 = min(N - 1, int(np.ceil(x1 / size * (N - 1))))
    j0 = max(0, int(np.floor(y0 / size * (N - 1))))
    j1 = min(N - 1, int(np.ceil(y1 / size * (N - 1))))

    xs = np.linspace(0, size, N)[i0:i1 + 1]
    ys = np.linspace(0, size, N)[j0:j1 + 1]
    sub = z_mm[j0:j1 + 1, i0:i1 + 1]
    nx, ny = len(xs), len(ys)
    gx, gy = np.meshgrid(xs, ys)

    top = np.column_stack([gx.ravel(), gy.ravel(), sub.ravel()])
    # top surface faces (two triangles per grid cell)
    ii, jj = np.meshgrid(np.arange(nx - 1), np.arange(ny - 1))
    v00 = (jj * nx + ii).ravel()
    v10 = v00 + 1
    v01 = v00 + nx
    v11 = v01 + 1
    faces = [np.column_stack([v00, v10, v11]), np.column_stack([v00, v11, v01])]

    # bottom plane at z=0 + side skirts around the perimeter -> watertight solid
    nverts = len(top)
    bottom = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(nverts)])
    verts = np.vstack([top, bottom])

    border = (_edge(0, nx, ny, "bottom") + _edge(nx - 1, nx, ny, "right")
              + _edge(ny - 1, nx, ny, "top") + _edge(0, nx, ny, "left"))
    for a, b in zip(border, border[1:] + border[:1]):
        faces.append(np.array([[a, b, b + nverts], [a, b + nverts, a + nverts]]))

    # flat bottom (reuse the perimeter loop, earcut the rectangle)
    loop = np.array(border)
    ring2d = bottom[loop, :2]
    btri = earcut.triangulate_float64(np.ascontiguousarray(ring2d),
                                      np.array([len(ring2d)], np.uint32))
    if len(btri):
        faces.append(loop[btri.reshape(-1, 3)] + nverts)

    mesh = trimesh.Trimesh(vertices=verts, faces=np.vstack(faces), process=False)
    mesh.visual.face_colors = color
    bounds2d = (xs[0], ys[0], xs[-1], ys[-1])
    return mesh, bounds2d


def _edge(k, nx, ny, side):
    """Ordered vertex indices along one side of the grid (no shared corners)."""
    if side == "bottom":   # j=0, i=0..nx-2
        return [0 * nx + i for i in range(nx - 1)]
    if side == "right":    # i=nx-1, j=0..ny-2
        return [j * nx + (nx - 1) for j in range(ny - 1)]
    if side == "top":      # j=ny-1, i=nx-1..1
        return [(ny - 1) * nx + i for i in range(nx - 1, 0, -1)]
    if side == "left":     # i=0, j=ny-1..1
        return [j * nx + 0 for j in range(ny - 1, 0, -1)]


# --------------------------------------------------------------------------- #
# Buildings
# --------------------------------------------------------------------------- #
def buildings_tile(cfg, sampler: TerrainSampler, polys, color):
    bcfg = cfg["features"]["buildings"]
    default_h = float(bcfg["default_height_m"])
    min_foot = float(bcfg["min_footprint_mm"])
    min_h = float(bcfg.get("min_height_mm", 0.0))
    embed = float(bcfg["embed_mm"])
    hscale = float(cfg["model"]["building_height_scale"])
    vscale = cfg.scale * hscale

    pieces = []
    for poly, height_m in polys:
        # Thicken sliver footprints so they survive a 0.4 mm nozzle.
        minx, miny, maxx, maxy = poly.bounds
        short = min(maxx - minx, maxy - miny)
        if short < min_foot:
            poly = poly.buffer((min_foot - short) / 2.0, join_style=2)
            if poly.is_empty:
                continue
            if poly.geom_type == "MultiPolygon":
                poly = max(poly.geoms, key=lambda g: g.area)
        cx, cy = poly.centroid.x, poly.centroid.y
        ground = float(sampler(cx, cy)[0])
        # True scaled height with a printable floor (matches map2model's
        # minBuildingHeightMM), so the city reads as texture rather than spikes.
        h_mm = max((height_m if height_m else default_h) * vscale, min_h)
        piece = _prism(poly, z_bottom=ground - embed, z_top=ground + h_mm)
        if piece:
            pieces.append(piece)
    return _combine(pieces, color)


# --------------------------------------------------------------------------- #
# Draped coloured slabs (water / trees / streets)
# --------------------------------------------------------------------------- #
def _drape(poly, sampler, offset, thickness, flat_z=None):
    """Thin slab over a polygon. If flat_z is set, top is flat (water)."""
    if poly.is_empty or poly.area <= 0:
        return None
    verts2d, _ = _rings(poly)
    if flat_z is not None:
        z_top = float(flat_z)
        z_bot = z_top - thickness
    else:
        z_top = sampler(verts2d[:, 0], verts2d[:, 1]) + offset
        z_bot = z_top - thickness
    return _prism(poly, z_bottom=z_bot, z_top=z_top)


# Superimposed sine waves -> a calm sea with swells and a cross-current.
# (wavelength_mm, relative_amplitude, direction_rad, phase)
_WAVE_COMPONENTS = [
    (175.0, 1.00, math.radians(25),  0.0),   # long swell / main current
    (135.0, 0.70, math.radians(50),  1.7),   # second current direction
    (95.0,  0.60, math.radians(115), 2.1),
    (60.0,  0.50, math.radians(70),  1.3),
    (38.0,  0.35, math.radians(20),  0.7),
    (24.0,  0.22, math.radians(160), 3.0),   # short chop
]


def _waved_slab(poly, z_bottom, wave_fn, max_area):
    """Slab with a flat bottom and a wave-modulated top over a polygon.

    Triangulated with the `triangle` engine so the surface carries interior
    vertices (earcut only meshes the outline, which can't show waves).
    """
    if poly.is_empty or poly.area <= 0 or poly.geom_type != "Polygon":
        return None
    try:
        v2d, faces = trimesh.creation.triangulate_polygon(
            poly, triangle_args=f"pa{max_area:.3f}", engine="triangle")
    except Exception:
        return None
    if faces is None or len(faces) == 0:
        return None
    faces = np.asarray(faces, dtype=np.int64)
    n = len(v2d)
    verts = np.vstack([np.column_stack([v2d, wave_fn(v2d)]),
                       np.column_stack([v2d, np.full(n, z_bottom)])])
    out = [faces, faces[:, ::-1] + n]                  # top up, bottom reversed
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    uniq, cnt = np.unique(edges, axis=0, return_counts=True)
    a, b = uniq[cnt == 1].T                             # boundary edges -> walls
    out.append(np.column_stack([a, b, b + n]))
    out.append(np.column_stack([a, b + n, a + n]))
    return verts, np.vstack(out)


def water_tile(cfg, sampler, polys, color):
    """Uniform flat-bottomed black water layer, optionally with a wave top.

    The bottom is always one flat plane at `level - thickness` (uniform layer
    height); only the top is modulated, so even with waves the water reads as a
    single calm sheet rather than varying in height per body.
    """
    wcfg = cfg["features"]["water"]
    level = float(wcfg["level_mm"])
    thick = float(wcfg["thickness_mm"])
    waves = wcfg.get("waves", {})

    if not waves.get("enabled"):
        pieces = [_drape(p, sampler, 0.0, thick, flat_z=level) for p in polys]
        return _combine(pieces, color)

    amp = float(waves["amplitude_mm"])
    max_area = float(waves.get("mesh_max_area_mm2", 4.0))
    norm = amp / sum(a for _, a, _, _ in _WAVE_COMPONENTS)

    def wave_fn(v2d):
        h = np.zeros(len(v2d))
        for wl, a, ang, ph in _WAVE_COMPONENTS:
            k = 2.0 * math.pi / wl
            h += a * np.sin(k * (v2d[:, 0] * math.cos(ang)
                                 + v2d[:, 1] * math.sin(ang)) + ph)
        return level + h * norm

    z_bottom = level - thick
    pieces = [_waved_slab(p, z_bottom, wave_fn, max_area) for p in polys]
    return _combine(pieces, color)


def trees_tile(cfg, sampler, polys, color):
    tcfg = cfg["features"]["trees"]
    off = float(tcfg["canopy_offset_mm"])
    thick = float(tcfg["thickness_mm"])
    pieces = [_drape(p, sampler, off, thick) for p in polys]
    return _combine(pieces, color)


def streets_tile(cfg, sampler, road_polys, color):
    rcfg = cfg["features"]["roads"]
    off = float(rcfg["offset_mm"])
    thick = float(rcfg["thickness_mm"])
    pieces = [_drape(p, sampler, off, thick) for p in road_polys]
    return _combine(pieces, color)


def roads_to_polygons(cfg, roads):
    """Buffer road centrelines to scaled width; drop anything below the nozzle min."""
    rcfg = cfg["features"]["roads"]
    widths = rcfg["class_width_m"]
    default_w = float(rcfg["default_width_m"])
    min_feat = float(cfg["features"]["min_feature_mm"])
    out, dropped = [], 0
    for line, cls in roads:
        w_mm = widths.get(cls, default_w) * cfg.scale
        if w_mm < min_feat:
            dropped += 1
            continue
        poly = line.buffer(w_mm / 2.0, cap_style=1, join_style=1)
        for p in (poly.geoms if poly.geom_type == "MultiPolygon" else [poly]):
            if not p.is_empty and p.area > 0:
                out.append(p)
    print(f"Streets   : {len(out)} kept, {dropped} below {min_feat} mm dropped")
    return out


# --------------------------------------------------------------------------- #
# Frame (black border on the outer edges of the assembled piece)
# --------------------------------------------------------------------------- #
def frame_tile(cfg, row, col, x0, x1, y0, y1, color):
    fcfg = cfg["frame"]
    if not fcfg["enabled"]:
        return None
    w = float(fcfg["width_mm"])
    h = float(fcfg["height_mm"])
    rows, cols = cfg["tiling"]["rows"], cfg["tiling"]["cols"]

    bars = []
    if col == 0:            # outer left
        bars.append(box(x0, y0, x0 + w, y1))
    if col == cols - 1:     # outer right
        bars.append(box(x1 - w, y0, x1, y1))
    if row == 0:            # outer bottom (south)
        bars.append(box(x0, y0, x1, y0 + w))
    if row == rows - 1:     # outer top (north)
        bars.append(box(x0, y1 - w, x1, y1))

    pieces = [_prism(b, 0.0, h) for b in bars]
    return _combine(pieces, color)
