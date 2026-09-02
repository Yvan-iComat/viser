"""Gallery preview

Show a grid of clickable preview blocks, like a parts library.

Each block is a framed snapshot with a title and an optional subtitle. The
number of blocks per row is derived by the client from ``block_width`` and the
available width, so the grid reflows to fill its space.

* :meth:`viser.GuiApi.add_gallery` to create the gallery, either as a
  full-window overlay (the default) or inside a panel via ``parent=``
* :meth:`viser.GuiGalleryHandle.add_block` to add blocks, from either a numpy
  image or an STL mesh (rendered to a snapshot in the browser)
* :meth:`viser.GalleryBlockHandle.on_click` for per-block click events
* :meth:`viser.GuiGalleryHandle.on_click` for gallery-wide click events
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

import viser

ASSETS = Path(__file__).parent / "gallery_assets"


def checkerboard(color: tuple[int, int, int]) -> np.ndarray:
    """A placeholder image, for blocks that aren't backed by a mesh."""
    image = np.zeros((160, 220, 3), dtype=np.uint8)
    tile = (np.indices((160, 220)).sum(axis=0) // 20) % 2
    image[tile == 0] = color
    image[tile == 1] = (245, 246, 248)
    return image


def main() -> None:
    server = viser.ViserServer()

    gallery = server.gui.add_gallery(
        "Parts",
        block_width=220,
        block_height=160,
        gap=16,
    )

    # Blocks backed by plain numpy images need no client, so they can be added
    # immediately.
    for name, color in [
        ("Latch Bracket", (168, 173, 181)),
        ("Sensor Mount", (150, 160, 175)),
        ("Shaft Cam Lock", (180, 172, 160)),
    ]:
        gallery.add_block(name, "Rachel DaBrosio", image=checkerboard(color))

    # Blocks backed by STL meshes are rendered offscreen by the browser, so
    # they need a connected client. `on_client_connect` fires once the client
    # is ready to handle messages -- polling `get_clients()` would return as
    # soon as the socket opens, which can be too early for the render request.
    stl_blocks_added = threading.Event()

    @server.on_client_connect
    def _(client: viser.ClientHandle) -> None:
        # Only build the STL blocks for the first client; later clients see the
        # gallery replayed from server state.
        if stl_blocks_added.is_set():
            return
        stl_blocks_added.set()

        for stl_path, author in [
            (ASSETS / "Motorcycle_Brake_Lever.STL", "James Rutherford"),
            (ASSETS / "Needle_Bearing_Block_v16.stl", "Connor Green"),
            (ASSETS / "drill-fixture-guide-v4.stl", "Brianna Stenford"),
        ]:
            title = stl_path.stem.replace("_", " ").replace("-", " ").title()
            block = gallery.add_block(title, author, stl=stl_path, timeout=30.0)

            @block.on_click
            def _(event: viser.GalleryBlockEvent) -> None:
                print(f"Opening mesh part: {event.target.title}")

    # Gallery-wide handler: fires for every block, after any per-block handler.
    @gallery.on_click
    def _(event: viser.GalleryBlockEvent) -> None:
        print(f"Clicked {event.target.title!r} (block {event.block_id[:8]})")

    # The same gallery can also live inside a movable panel instead of covering
    # the window: pass a container (here a panel's tab) as `parent`.
    panel = server.gui.add_panel()
    docked = server.gui.add_gallery(
        "Favorites",
        parent=panel.add_tab("Favorites"),
        block_width=110,
        block_height=80,
        gap=8,
    )
    for name in ["Latch Arm", "Header Flange"]:
        docked.add_block(name, image=checkerboard((160, 168, 180)))

    @docked.on_click
    def _(event: viser.GalleryBlockEvent) -> None:
        print(f"Favorite: {event.target.title}")

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    main()
