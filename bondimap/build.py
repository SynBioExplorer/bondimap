"""Orchestrator: data -> per-tile coloured meshes -> .3mf files."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from shapely import STRtree
from shapely.geometry import box

from . import config, elevation, mesh, overture
from .export import write_3mf

CATEGORIES = ["terrain", "water", "trees", "buildings", "streets", "frame"]


def _rgba(hex_color: str):
    c = hex_color.lstrip("#")
    return [int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255]


def _poly_parts(g):
    t = g.geom_type
    if t == "Polygon":
        return [g] if g.area > 0 else []
    if t == "MultiPolygon":
        return [p for p in g.geoms if p.area > 0]
    if t == "GeometryCollection":
        out = []
        for sub in g.geoms:
            out += _poly_parts(sub)
        return out
    return []


def _carve_seabed(z_mm, water_polys, size, seabed_z):
    """Lower terrain to a flat seabed wherever there is water (model mm).

    Rasterises the water polygons onto the height grid and clamps those cells
    down to the base level, so white land can't poke up through the black sea
    near coastlines and the water slab rests on a clean flat bottom.
    """
    if not water_polys:
        return z_mm
    from PIL import Image, ImageDraw

    n = z_mm.shape[0]
    s = (n - 1) / size
    img = Image.new("L", (n, n), 0)
    draw = ImageDraw.Draw(img)
    for poly in water_polys:
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        draw.polygon([(x * s, y * s) for x, y in poly.exterior.coords], fill=255)
        for ring in poly.interiors:
            draw.polygon([(x * s, y * s) for x, y in ring.coords], fill=0)
    mask = np.asarray(img) > 127
    z_mm[mask] = np.minimum(z_mm[mask], seabed_z)
    print(f"Seabed    : flattened {int(mask.sum()):,} grid cells under water")
    return z_mm


def _clip(tree, geoms, attrs, tilebox):
    """Clip a category's geoms to the tile box using a prebuilt STRtree."""
    if not geoms:
        return [], []
    out_g, out_a = [], []
    for k in tree.query(tilebox, predicate="intersects"):
        k = int(k)
        for part in _poly_parts(geoms[k].intersection(tilebox)):
            out_g.append(part)
            out_a.append(attrs[k] if attrs is not None else None)
    return out_g, out_a


def run(config_path):
    t0 = time.time()
    cfg = config.load(config_path)
    colors = {c: _rgba(cfg["colors"][c]) for c in CATEGORIES}

    # --- terrain ---
    z_mm = elevation.build_terrain(cfg)

    # --- vector layers (full area, model-mm coords) ---
    buildings = overture.fetch_buildings(cfg)
    roads = overture.fetch_roads(cfg)
    water = overture.fetch_water(cfg)
    green = overture.fetch_green(cfg)

    # Flatten a seabed under the water before sampling terrain heights.
    z_mm = _carve_seabed(z_mm, water, cfg.size_mm,
                         float(cfg["model"]["base_thickness_mm"]))
    sampler = elevation.TerrainSampler(cfg, z_mm)
    road_polys = mesh.roads_to_polygons(cfg, roads)

    b_geoms = [g for g, _ in buildings]
    b_h = [h for _, h in buildings]
    layers = {
        "buildings": (STRtree(b_geoms) if b_geoms else None, b_geoms, b_h),
        "streets": (STRtree(road_polys) if road_polys else None, road_polys, None),
        "water": (STRtree(water) if water else None, water, None),
        "trees": (STRtree(green) if green else None, green, None),
    }

    # --- tiles ---
    rows, cols = cfg["tiling"]["rows"], cfg["tiling"]["cols"]
    size = cfg.size_mm
    out_dir = Path(cfg.path.parent, cfg["output"]["dir"])
    name = cfg["output"]["name"]
    print(f"\nTiling    : {rows}x{cols} -> {rows*cols} files, "
          f"{size/cols:.0f} x {size/rows:.0f} mm each\n")

    for r in range(rows):
        for c in range(cols):
            x0, x1 = c * size / cols, (c + 1) * size / cols
            y0, y1 = r * size / rows, (r + 1) * size / rows
            tilebox = box(x0, y0, x1, y1)
            label = f"r{r}c{c}"
            print(f"Tile {label}: model [{x0:.0f},{x1:.0f}] x [{y0:.0f},{y1:.0f}] mm")

            terrain_mesh, _ = mesh.terrain_tile(cfg, z_mm, x0, x1, y0, y1, colors["terrain"])

            # Clip every layer to the tile box. Besides splitting tiles, this
            # crops features that merely overlap the area (ocean, long roads,
            # large land polygons) so nothing sprawls past the model border.
            bt, bg, bh = layers["buildings"]
            bpolys, bheights = _clip(bt, bg, bh, tilebox) if bt else ([], [])

            st, sg, _ = layers["streets"]
            spolys, _ = _clip(st, sg, None, tilebox) if st else ([], [])

            wt, wg, _ = layers["water"]
            wpolys, _ = _clip(wt, wg, None, tilebox) if wt else ([], [])

            gt, gg, _ = layers["trees"]
            gpolys, _ = _clip(gt, gg, None, tilebox) if gt else ([], [])

            meshes = {
                "terrain": terrain_mesh,
                "water": mesh.water_tile(cfg, sampler, wpolys, colors["water"]),
                "trees": mesh.trees_tile(cfg, sampler, gpolys, colors["trees"]),
                "buildings": mesh.buildings_tile(
                    cfg, sampler, list(zip(bpolys, bheights)), colors["buildings"]),
                "streets": mesh.streets_tile(cfg, sampler, spolys, colors["streets"]),
                "frame": mesh.frame_tile(cfg, r, c, x0, x1, y0, y1, colors["frame"]),
            }
            write_3mf(out_dir / f"{name}_{label}.3mf", meshes, cfg["colors"])

            if cfg["output"].get("preview", True):
                from .preview import save_preview
                save_preview(out_dir / f"{name}_{label}.png", (x0, x1), (y0, y1),
                             cfg["colors"], wpolys, gpolys, spolys, bpolys,
                             z_mm=z_mm, size=size)

    print(f"\nDone in {time.time() - t0:.0f}s -> {out_dir}")
