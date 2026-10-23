"""E2E tests for file drop zones (``GuiApi.add_file_drop_zone``).

Drops are simulated by dispatching DragEvents carrying a real DataTransfer
(Playwright has no native OS file drag), at a viewport point."""

from __future__ import annotations

import threading

from playwright.sync_api import Page, expect

import viser

_DROP_SCRIPT = """
({ name, content, x, y }) => {
  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(new File([content], name));
  const target = document.elementFromPoint(x, y) ?? document.body;
  const init = {
    bubbles: true,
    cancelable: true,
    dataTransfer,
    clientX: x,
    clientY: y,
  };
  target.dispatchEvent(new DragEvent("dragenter", init));
  target.dispatchEvent(new DragEvent("dragover", init));
  target.dispatchEvent(new DragEvent("drop", init));
}
"""


def _drop(page: Page, name: str, content: str, x: float, y: float) -> None:
    page.evaluate(_DROP_SCRIPT, {"name": name, "content": content, "x": x, "y": y})


def _viewport_center(page: Page) -> tuple[float, float]:
    size = page.viewport_size
    assert size is not None
    return size["width"] / 2, size["height"] / 2


def test_drop_accepted_file_reaches_callback(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    received: list[viser.FileDropEvent] = []
    fired = threading.Event()
    zone = viser_server.gui.add_file_drop_zone(
        (".step", ".stp", ".stl", ".obj"), text="Drag and drop files here."
    )

    @zone.on_drop
    def _(event: viser.FileDropEvent) -> None:
        received.append(event)
        fired.set()

    expect(viser_page.get_by_text("Drag and drop files here.")).to_be_visible(
        timeout=5_000
    )
    expect(viser_page.get_by_text(".step, .stp, .stl, .obj")).to_be_visible()

    x, y = _viewport_center(viser_page)
    _drop(viser_page, "bracket.STL", "solid bracket", x, y)

    assert fired.wait(timeout=5.0), "on_drop was not triggered"
    assert received[0].file.name == "bracket.STL"
    assert received[0].file.content == b"solid bracket"


def test_drop_rejected_extension_notifies_and_skips_callback(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    fired = threading.Event()
    zone = viser_server.gui.add_file_drop_zone((".stl",))
    zone.on_drop(lambda _: fired.set())

    expect(viser_page.get_by_text("Drag and drop files here.")).to_be_visible(
        timeout=5_000
    )
    x, y = _viewport_center(viser_page)
    _drop(viser_page, "notes.txt", "hello", x, y)

    expect(viser_page.get_by_text("Unsupported file: notes.txt")).to_be_visible(
        timeout=5_000
    )
    assert not fired.wait(timeout=0.5)


def test_drop_outside_zone_is_ignored(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    fired = threading.Event()
    zone = viser_server.gui.add_file_drop_zone((".stl",))
    zone.on_drop(lambda _: fired.set())

    expect(viser_page.get_by_text("Drag and drop files here.")).to_be_visible(
        timeout=5_000
    )
    # Inside the viewer's top-left margin, outside the zone's inset rectangle.
    _drop(viser_page, "part.stl", "solid", 2, 2)
    assert not fired.wait(timeout=0.5)


def test_hidden_border_still_accepts_drops(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    fired = threading.Event()
    zone = viser_server.gui.add_file_drop_zone(
        (".obj",), text="Hidden drop zone", show_border=False
    )
    zone.on_drop(lambda _: fired.set())

    text = viser_page.get_by_text("Hidden drop zone")
    expect(text).to_be_attached(timeout=5_000)
    # The rectangle is transparent at rest...
    rect = text.locator("xpath=ancestor::div[2]")
    expect(rect).to_have_css("opacity", "0")

    # ...but drops inside it are still delivered.
    x, y = _viewport_center(viser_page)
    _drop(viser_page, "mesh.obj", "v 0 0 0", x, y)
    assert fired.wait(timeout=5.0), "on_drop was not triggered"

    # Toggling the option live shows the rectangle.
    zone.show_border = True
    expect(rect).to_have_css("opacity", "1", timeout=5_000)


def test_removed_zone_disappears(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    zone = viser_server.gui.add_file_drop_zone((".stl",), text="Temporary zone")
    expect(viser_page.get_by_text("Temporary zone")).to_be_visible(timeout=5_000)
    zone.remove()
    expect(viser_page.get_by_text("Temporary zone")).to_have_count(0, timeout=5_000)
