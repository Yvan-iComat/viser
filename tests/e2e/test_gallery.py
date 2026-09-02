"""E2E tests for the gallery preview component."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from playwright.sync_api import Page, expect

import viser

STL_ASSET = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "02_gui"
    / "gallery_assets"
    / "Needle_Bearing_Block_v16.stl"
)


def _image(color: tuple[int, int, int] = (120, 130, 140)) -> np.ndarray:
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[:, :] = color
    return image


def test_gallery_renders_titles_and_subtitles(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """Blocks render their title, and their subtitle when one is set."""
    gallery = viser_server.gui.add_gallery()
    gallery.add_block("Actuator Housing", "Simon Slater", image=_image())
    gallery.add_block("No Subtitle Part", image=_image())

    expect(viser_page.get_by_text("Actuator Housing")).to_be_visible(timeout=10_000)
    expect(viser_page.get_by_text("Simon Slater")).to_be_visible(timeout=5_000)
    expect(viser_page.get_by_text("No Subtitle Part")).to_be_visible(timeout=5_000)


def test_gallery_block_click_fires_callbacks(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """Clicking a block fires its own callback, then the gallery-wide one."""
    gallery = viser_server.gui.add_gallery()
    block = gallery.add_block("Clickable Part", "subtitle", image=_image())
    other = gallery.add_block("Other Part", image=_image())

    order: list[str] = []
    clicked = threading.Event()

    @block.on_click
    def _(event: viser.GalleryBlockEvent) -> None:
        order.append(f"block:{event.target.title}")

    @other.on_click
    def _(event: viser.GalleryBlockEvent) -> None:
        order.append("WRONG_BLOCK")

    @gallery.on_click
    def _(event: viser.GalleryBlockEvent) -> None:
        order.append(f"gallery:{event.target.title}")
        clicked.set()

    # The GPU-less test runner shows a "WebGL is running in software mode"
    # toast in the top-left, which overlaps the first block. Dismiss any
    # notifications first so the click lands on the card.
    viser_page.keyboard.press("Escape")
    for close in viser_page.get_by_role("button", name="Close").all():
        try:
            close.click(timeout=1_000)
        except Exception:
            pass

    target = viser_page.get_by_role("button", name="Clickable Part")
    target.wait_for(timeout=10_000)
    # dispatch_event bypasses the notification toast that overlaps the first
    # block on GPU-less runners; a real pointer click would be intercepted.
    target.dispatch_event("click", event_init={"button": 0})

    assert clicked.wait(timeout=10.0), "click callback never fired"
    # Per-block callback runs before the gallery-wide one, and the sibling
    # block's callback must not fire.
    assert order == ["block:Clickable Part", "gallery:Clickable Part"]


def test_gallery_block_updates_propagate(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """Retitling and removing blocks is reflected in the browser."""
    gallery = viser_server.gui.add_gallery()
    block = gallery.add_block("Original Title", "Original Sub", image=_image())
    gallery.add_block("Second Block", image=_image())

    expect(viser_page.get_by_text("Original Title")).to_be_visible(timeout=10_000)

    block.title = "Updated Title"
    block.subtitle = "Updated Sub"
    expect(viser_page.get_by_text("Updated Title")).to_be_visible(timeout=5_000)
    expect(viser_page.get_by_text("Updated Sub")).to_be_visible(timeout=5_000)
    expect(viser_page.get_by_text("Original Title")).to_be_hidden(timeout=5_000)

    block.remove()
    expect(viser_page.get_by_text("Updated Title")).to_be_hidden(timeout=5_000)
    # The sibling survives the removal.
    expect(viser_page.get_by_text("Second Block")).to_be_visible(timeout=5_000)


def test_gallery_stl_snapshot_round_trip(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """An STL block is rendered by the browser and the PNG comes back.

    This is the full round trip: server sends STL bytes, the client renders
    them offscreen, and the reply populates the block's image.
    """
    assert STL_ASSET.exists(), f"missing test asset: {STL_ASSET}"
    gallery = viser_server.gui.add_gallery()

    # add_block blocks until the snapshot arrives (or the timeout elapses).
    block = gallery.add_block("Bearing Block", "from STL", stl=STL_ASSET, timeout=30.0)

    stored = gallery.blocks[0]
    assert stored.block_id == block.block_id
    assert stored._data is not None, "no snapshot came back from the client"
    assert stored._format == "png"
    # A real render, not a blank/degenerate payload.
    assert stored._data[:8] == b"\x89PNG\r\n\x1a\n", "reply is not a PNG"
    assert len(stored._data) > 1000, "snapshot suspiciously small"

    expect(viser_page.get_by_text("Bearing Block")).to_be_visible(timeout=10_000)
    expect(viser_page.get_by_role("img", name="Bearing Block")).to_be_visible(
        timeout=10_000
    )


def test_gallery_inline_in_panel_renders_and_clicks(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """A gallery placed in a panel tab renders there and stays clickable."""
    panel = viser_server.gui.add_panel()
    tab = panel.add_tab("Parts")
    gallery = viser_server.gui.add_gallery(parent=tab, block_width=120)
    assert gallery.placement == "inline"
    block = gallery.add_block("Panel Part", "in a tab", image=_image())

    clicked = threading.Event()

    @block.on_click
    def _(event: viser.GalleryBlockEvent) -> None:
        clicked.set()

    expect(viser_page.get_by_text("Panel Part")).to_be_visible(timeout=10_000)
    expect(viser_page.get_by_text("in a tab")).to_be_visible(timeout=5_000)

    # Retry the dispatch: the panel's pane can still be settling when the block
    # first paints, and a click landing in that window is dropped before React
    # attaches the handler. Re-dispatching is safe -- a duplicate click would
    # only set the same event again.
    target = viser_page.get_by_role("button", name="Panel Part")
    for _attempt in range(10):
        target.dispatch_event("click", event_init={"button": 0})
        if clicked.wait(timeout=1.0):
            break
    assert clicked.is_set(), "inline gallery click never fired"


def test_docked_panel_visible_over_window_gallery(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """A panel docked left must stay visible on top of a full-window gallery.

    Regression: the gallery overlay was a sibling AFTER the dock surface at
    zIndex 10, so it painted over docked panels (which made a panel look like
    it disappeared when docked) and swallowed their drag targets.
    """
    gallery = viser_server.gui.add_gallery()
    gallery.add_block("Behind Panel", image=_image())

    panel = viser_server.gui.add_panel()
    with panel.add_tab("Docked"):
        viser_server.gui.add_button("Panel Button")

    expect(viser_page.get_by_text("Behind Panel")).to_be_visible(timeout=10_000)

    panel.dock_left()

    # The panel's content stays visible and hit-testable over the gallery.
    button = viser_page.get_by_role("button", name="Panel Button")
    expect(button).to_be_visible(timeout=10_000)
    viser_page.wait_for_timeout(500)
    # A real pointer click: proves the gallery isn't covering the panel's
    # hit area, which `to_be_visible` alone would not catch.
    button.click(timeout=10_000)
    # The gallery is still there, inset beside the docked region.
    expect(viser_page.get_by_text("Behind Panel")).to_be_visible(timeout=5_000)


def test_gallery_hidden_does_not_block_canvas(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """A hidden gallery must not leave an opaque overlay over the scene."""
    gallery = viser_server.gui.add_gallery()
    gallery.add_block("Hidden Part", image=_image())
    expect(viser_page.get_by_text("Hidden Part")).to_be_visible(timeout=10_000)

    gallery.visible = False
    expect(viser_page.get_by_text("Hidden Part")).to_be_hidden(timeout=5_000)

    gallery.visible = True
    expect(viser_page.get_by_text("Hidden Part")).to_be_visible(timeout=5_000)
