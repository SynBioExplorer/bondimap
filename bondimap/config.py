"""Configuration loading and derived geometry (CRS, bbox, model scale).

All downstream code works in "model millimetres": a planar coordinate system
whose origin is the south-west corner of the area and whose units are mm on the
printed model. The mapping is a fixed scale from local UTM metres, so 1 mm on
the model is always the same number of ground metres everywhere.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer


@dataclass
class Config:
    raw: dict
    path: Path

    # --- area / projection (filled in __post_init__) ---
    utm_crs: CRS = field(init=False)
    to_utm: Transformer = field(init=False)
    to_wgs: Transformer = field(init=False)
    origin_utm: tuple[float, float] = field(init=False)  # SW corner (x, y) in UTM metres
    span_m: float = field(init=False)                    # ground metres across the square
    wgs_bbox: tuple[float, float, float, float] = field(init=False)  # west, south, east, north

    def __post_init__(self):
        area = self.raw["area"]

        if area.get("bbox"):
            west, south, east, north = area["bbox"]
            center_lon = 0.5 * (west + east)
            center_lat = 0.5 * (south + north)
        else:
            center_lat = area["center_lat"]
            center_lon = area["center_lon"]

        # Auto-pick the local UTM zone so this works for any city, not just Sydney.
        zone = int((center_lon + 180.0) / 6.0) + 1
        epsg = (32700 if center_lat < 0 else 32600) + zone
        self.utm_crs = CRS.from_epsg(epsg)
        self.to_utm = Transformer.from_crs("EPSG:4326", self.utm_crs, always_xy=True)
        self.to_wgs = Transformer.from_crs(self.utm_crs, "EPSG:4326", always_xy=True)

        cx, cy = self.to_utm.transform(center_lon, center_lat)

        if area.get("bbox"):
            # Project the requested lon/lat box and take a square that covers it.
            xs, ys = self.to_utm.transform([west, east, west, east], [south, south, north, north])
            half = 0.5 * max(max(xs) - min(xs), max(ys) - min(ys))
        else:
            half = 0.5 * float(area["span_m"])

        self.span_m = 2.0 * half
        self.origin_utm = (cx - half, cy - half)

        # WGS84 bounding box covering the UTM square (sample the perimeter, not just
        # corners, so the curved meridians are fully enclosed).
        t = np.linspace(0.0, 1.0, 9)
        edge_x, edge_y = [], []
        for ax, ay, bx, by in [
            (cx - half, cy - half, cx + half, cy - half),
            (cx + half, cy - half, cx + half, cy + half),
            (cx + half, cy + half, cx - half, cy + half),
            (cx - half, cy + half, cx - half, cy - half),
        ]:
            edge_x.extend(ax + (bx - ax) * t)
            edge_y.extend(ay + (by - ay) * t)
        lons, lats = self.to_wgs.transform(edge_x, edge_y)
        self.wgs_bbox = (min(lons), min(lats), max(lons), max(lats))

    # --- convenience accessors ---
    @property
    def scale(self) -> float:
        """mm on the model per metre on the ground."""
        return self.raw["model"]["size_mm"] / self.span_m

    @property
    def size_mm(self) -> float:
        return self.raw["model"]["size_mm"]

    def utm_to_model(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ox, oy = self.origin_utm
        return (np.asarray(x) - ox) * self.scale, (np.asarray(y) - oy) * self.scale

    def model_to_utm(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ox, oy = self.origin_utm
        return np.asarray(x) / self.scale + ox, np.asarray(y) / self.scale + oy

    def __getitem__(self, key):
        return self.raw[key]


def load(path: str | Path) -> Config:
    path = Path(path)
    raw = json.loads(path.read_text())
    cfg = Config(raw=raw, path=path)

    w, s, e, n = cfg.wgs_bbox
    print(f"Area      : center ~ ({0.5*(s+n):.4f}, {0.5*(w+e):.4f})  span {cfg.span_m:.0f} m")
    print(f"Model     : {cfg.size_mm:.0f} mm  ->  scale {cfg.scale*1000:.3f} mm/km "
          f"({1.0/cfg.scale:.0f} m of ground per mm)")
    print(f"UTM CRS   : {cfg.utm_crs.to_epsg()}")
    print(f"WGS bbox  : {w:.4f}, {s:.4f}, {e:.4f}, {n:.4f}")
    return cfg
