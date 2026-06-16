"""Terrain: fetch terrarium tiles, decode to metres, resample to the model grid.

Mapterhorn and the AWS Mapzen set both use the *terrarium* encoding:
    elevation_m = R * 256 + G + B / 256 - 32768
Tiles are fetched at a slippy-map zoom, mosaicked in Web-Mercator pixel space,
then sampled at the model grid points (which live in local UTM, reprojected to
lon/lat for the lookup). The result is a height field in model millimetres.
"""

from __future__ import annotations

import io
import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates

from .config import Config

TILE = 512  # Mapterhorn tile size; AWS PNGs are 256 and are upsampled on decode.
_HEADERS = {"User-Agent": "bondimap/1.0 (3d-print map; personal use)"}


def _lonlat_to_pixel(lon, lat, z):
    """Global Web-Mercator pixel coords at zoom z (TILE-sized tiles)."""
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n * TILE
    lat_r = np.radians(lat)
    y = (1.0 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2.0 * n * TILE
    return x, y


def _decode_terrarium(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB"), dtype=np.float64)
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


def _fetch_tile(z, x, y, primary_url, fallback_url):
    """Return (tx, ty, elevation_512) or raise. Falls back to AWS on any miss."""
    for url in (primary_url, fallback_url):
        if not url:
            continue
        try:
            r = requests.get(url.format(z=z, x=x, y=y), headers=_HEADERS, timeout=30)
            if r.status_code != 200 or not r.content:
                continue
            img = Image.open(io.BytesIO(r.content))
            elev = _decode_terrarium(img)
            if elev.shape[0] != TILE:  # AWS PNG is 256 -> upsample to the mosaic grid
                img = img.resize((TILE, TILE), Image.BILINEAR)
                elev = _decode_terrarium(img)
            return x, y, elev
        except Exception:
            continue
    # No data anywhere for this tile (e.g. open ocean): treat as sea level.
    return x, y, np.zeros((TILE, TILE), dtype=np.float64)


def build_terrain(cfg: Config) -> np.ndarray:
    """Return Z[j, i] in model mm, shape (N, N); i indexes +x (east), j indexes +y (north)."""
    z = int(cfg["elevation"]["zoom"])
    west, south, east, north = cfg.wgs_bbox

    # Tile range covering the bbox.
    x0p, y0p = _lonlat_to_pixel(west, north, z)   # NW
    x1p, y1p = _lonlat_to_pixel(east, south, z)   # SE
    tx0, tx1 = int(x0p // TILE), int(x1p // TILE)
    ty0, ty1 = int(y0p // TILE), int(y1p // TILE)
    n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    print(f"Terrain   : zoom {z}, fetching {n_tiles} tile(s) "
          f"[{tx0}-{tx1}] x [{ty0}-{ty1}]")

    mosaic = np.zeros(((ty1 - ty0 + 1) * TILE, (tx1 - tx0 + 1) * TILE), dtype=np.float64)
    primary = cfg["elevation"]["mapterhorn_url"]
    fallback = cfg["elevation"]["fallback_url"]

    jobs = [(z, tx, ty, primary, fallback)
            for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for tx, ty, elev in ex.map(lambda a: _fetch_tile(*a), jobs):
            r = (ty - ty0) * TILE
            c = (tx - tx0) * TILE
            mosaic[r:r + TILE, c:c + TILE] = elev

    # Model grid points -> UTM -> lon/lat -> mosaic pixel -> bilinear sample.
    N = int(cfg["model"]["terrain_grid_per_side"])
    g = np.linspace(0.0, cfg.size_mm, N)
    mx, my = np.meshgrid(g, g)                       # model mm; my increases north
    ux, uy = cfg.model_to_utm(mx.ravel(), my.ravel())
    lon, lat = cfg.to_wgs.transform(ux, uy)
    px, py = _lonlat_to_pixel(np.asarray(lon), np.asarray(lat), z)
    px -= tx0 * TILE
    py -= ty0 * TILE
    elev = map_coordinates(mosaic, [py, px], order=1, mode="nearest").reshape(N, N)

    sigma = float(cfg["model"]["terrain_smoothing_sigma_px"])
    if sigma > 0:
        elev = gaussian_filter(elev, sigma=sigma)

    elev = np.clip(elev, 0.0, None)  # drop bathymetry; sea floor sits at datum 0
    exag = float(cfg["model"]["vertical_exaggeration"])
    base = float(cfg["model"]["base_thickness_mm"])
    z_mm = base + elev * cfg.scale * exag

    print(f"Terrain   : relief {elev.min():.0f}-{elev.max():.0f} m  ->  "
          f"model height {z_mm.min():.1f}-{z_mm.max():.1f} mm")
    return z_mm


class TerrainSampler:
    """Bilinear lookup of model-mm height at arbitrary model (x, y)."""

    def __init__(self, cfg: Config, z_mm: np.ndarray):
        self.z = z_mm
        self.N = z_mm.shape[0]
        self.size = cfg.size_mm

    def __call__(self, x, y) -> np.ndarray:
        # atleast_1d so scalar lookups (e.g. a building centroid) don't hand
        # map_coordinates a rank-0 coordinate array.
        x = np.atleast_1d(np.asarray(x, dtype=np.float64))
        y = np.atleast_1d(np.asarray(y, dtype=np.float64))
        fi = np.clip(x / self.size * (self.N - 1), 0, self.N - 1)
        fj = np.clip(y / self.size * (self.N - 1), 0, self.N - 1)
        return map_coordinates(self.z, [fj, fi], order=1, mode="nearest")
