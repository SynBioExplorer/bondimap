"""Top-down PNG preview of a tile, rendered from the clipped 2D layers.

Fast (polygons, not the 3D triangle soup) and a useful sanity check that the
map looks right before committing to a long print. Holes are ignored — this is
a preview, not the print geometry.
"""

from __future__ import annotations

import numpy as np


def save_preview(path, xlim, ylim, colors, water, trees, streets, buildings):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    fig, ax = plt.subplots(figsize=(9, 9), dpi=200)
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
    fig.savefig(path, dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  preview -> {path.name}")
