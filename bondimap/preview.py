"""Top-down PNG preview of a tile, rendered from the clipped 2D layers.

Fast (polygons, not the 3D triangle soup) and a useful sanity check that the
map looks right before committing to a long print. A hillshade of the terrain
is drawn underneath so the relief reads even when the category colours are
near-monochrome (e.g. white land / black water). Holes are ignored — this is a
preview, not the print geometry.
"""

from __future__ import annotations

import numpy as np


def save_preview(path, xlim, ylim, colors, water, trees, streets, buildings,
                 z_mm=None, size=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    fig, ax = plt.subplots(figsize=(9, 9), dpi=200)

    if z_mm is not None and size:
        from matplotlib.colors import LightSource
        spacing = size / (z_mm.shape[0] - 1)
        hs = LightSource(azdeg=315, altdeg=45).hillshade(
            z_mm, vert_exag=1.5, dx=spacing, dy=spacing)
        ax.imshow(hs, extent=[0, size, 0, size], origin="lower", cmap="gray",
                  vmin=0, vmax=1, zorder=0, interpolation="bilinear")
    else:
        fig.patch.set_facecolor(colors["terrain"])
        ax.set_facecolor(colors["terrain"])

    def add(polys, color, z):
        verts = [np.asarray(p.exterior.coords) for p in polys if not p.is_empty]
        if verts:
            ax.add_collection(PolyCollection(verts, facecolors=color,
                                             edgecolors="none", zorder=z))

    add(trees, colors["trees"], 1)
    add(water, colors["water"], 2)
    add(streets, colors["streets"], 3)
    add(buildings, colors["buildings"], 4)

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(path, dpi=200, facecolor="white")
    plt.close(fig)
    print(f"  preview -> {path.name}")
