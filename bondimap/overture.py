"""Overture Maps vector layers via DuckDB, returned in model-mm coordinates.

DuckDB reads the cloud GeoParquet over HTTP range requests and the WHERE clause
on the `bbox` struct prunes row groups, so only the area of interest is
transferred. Geometry comes back as WKB, is reprojected WGS84 -> UTM -> model mm,
and cleaned with buffer(0).
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely import wkb
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import transform as shp_transform

from .config import Config


def _connect(cfg: Config):
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"SET s3_region='{cfg['overture']['s3_region']}';")
    return con


def _path(cfg: Config, theme: str, type_: str) -> str:
    rel = cfg["overture"]["release"]
    return (f"s3://overturemaps-us-west-2/release/{rel}/"
            f"theme={theme}/type={type_}/*")


def _bbox_where(cfg: Config) -> str:
    w, s, e, n = cfg.wgs_bbox
    # Overlap test (not containment) so features crossing the edge are included.
    return (f"bbox.xmin <= {e} AND bbox.xmax >= {w} "
            f"AND bbox.ymin <= {n} AND bbox.ymax >= {s}")


def _to_model(cfg: Config, geom):
    """Reproject a WGS84 shapely geom into model-mm coordinates."""
    def fn(x, y, z=None):
        ux, uy = cfg.to_utm.transform(x, y)
        return cfg.utm_to_model(ux, uy)
    return shp_transform(fn, geom)


def _load_rows(con, sql, cfg, extra_cols):
    rows = con.execute(sql).fetchall()
    out = []
    for row in rows:
        geom = wkb.loads(bytes(row[0]))
        if geom.is_empty:
            continue
        geom = _to_model(cfg, geom)
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        out.append((geom, *row[1:1 + len(extra_cols)]))
    return out


def fetch_buildings(cfg: Config):
    """-> list of (polygon_model_mm, height_m or None)."""
    con = _connect(cfg)
    sql = f"""
        SELECT ST_AsWKB(geometry) AS wkb, height, num_floors, subtype
        FROM read_parquet('{_path(cfg, "buildings", "building")}',
                          filename=true, hive_partitioning=1)
        WHERE {_bbox_where(cfg)}
    """
    rows = _load_rows(con, sql, cfg, ["height", "num_floors", "subtype"])
    con.close()
    out = []
    for geom, height, floors, _sub in rows:
        h = height if height else (float(floors) * 3.2 if floors else None)
        for poly in _polys(geom):
            out.append((poly, h))
    print(f"Overture  : {len(out)} building footprint(s)")
    return out


def fetch_roads(cfg: Config):
    """-> list of (linestring_model_mm, class_str)."""
    con = _connect(cfg)
    sql = f"""
        SELECT ST_AsWKB(geometry) AS wkb, class, subtype
        FROM read_parquet('{_path(cfg, "transportation", "segment")}',
                          filename=true, hive_partitioning=1)
        WHERE {_bbox_where(cfg)} AND subtype = 'road'
    """
    rows = _load_rows(con, sql, cfg, ["class", "subtype"])
    con.close()
    out = [(geom, cls or "unclassified") for geom, cls, _sub in rows
           if geom.geom_type in ("LineString", "MultiLineString")]
    print(f"Overture  : {len(out)} road segment(s)")
    return out


def fetch_water(cfg: Config):
    """-> list of polygon_model_mm."""
    con = _connect(cfg)
    sql = f"""
        SELECT ST_AsWKB(geometry) AS wkb, subtype
        FROM read_parquet('{_path(cfg, "base", "water")}',
                          filename=true, hive_partitioning=1)
        WHERE {_bbox_where(cfg)}
    """
    rows = _load_rows(con, sql, cfg, ["subtype"])
    con.close()
    out = [p for geom, _sub in rows for p in _polys(geom)]
    print(f"Overture  : {len(out)} water polygon(s)")
    return out


def fetch_green(cfg: Config):
    """Parks / forest / grass etc. from base.land and base.land_use -> polygons."""
    wanted = set(cfg["overture"]["green_subtypes"])
    quoted = ", ".join(f"'{s}'" for s in wanted)
    con = _connect(cfg)
    out = []
    for type_ in ("land", "land_use"):
        try:
            sql = f"""
                SELECT ST_AsWKB(geometry) AS wkb, subtype, class
                FROM read_parquet('{_path(cfg, "base", type_)}',
                                  filename=true, hive_partitioning=1)
                WHERE {_bbox_where(cfg)}
                  AND (subtype IN ({quoted}) OR class IN ({quoted}))
            """
            rows = _load_rows(con, sql, cfg, ["subtype", "class"])
            out.extend(p for geom, *_ in rows for p in _polys(geom))
        except Exception as exc:
            print(f"Overture  : base/{type_} skipped ({exc})")
    con.close()
    print(f"Overture  : {len(out)} green polygon(s)")
    return out


def _polys(geom):
    if isinstance(geom, Polygon):
        return [geom] if geom.area > 0 else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if g.area > 0]
    return []
