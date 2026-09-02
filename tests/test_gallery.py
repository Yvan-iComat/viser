"""Unit tests for gallery handle bookkeeping."""

from __future__ import annotations

import threading

import numpy as np
import pytest

import viser


def _image(h: int = 8, w: int = 8, c: int = 3) -> np.ndarray:
    return np.zeros((h, w, c), dtype=np.uint8)


def test_add_and_remove_blocks() -> None:
    """Blocks are appended in order and removal only drops the named block."""
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery(block_width=200)
        assert gallery.blocks == ()

        a = gallery.add_block("A", "sub A", image=_image())
        b = gallery.add_block("B", image=_image())
        c = gallery.add_block("C", image=_image())

        assert [blk.title for blk in gallery.blocks] == ["A", "B", "C"]
        assert gallery.blocks[0].subtitle == "sub A"
        assert gallery.blocks[1].subtitle is None
        # Every block carries encoded image bytes.
        assert all(blk._data is not None for blk in gallery.blocks)

        b.remove()
        assert [blk.title for blk in gallery.blocks] == ["A", "C"]
        assert b.removed
        assert not a.removed and not c.removed

        gallery.clear()
        assert gallery.blocks == ()
        assert a.removed
    finally:
        server.stop()


def test_block_title_and_subtitle_roundtrip() -> None:
    """Mutating one block leaves its siblings untouched."""
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery()
        a = gallery.add_block("A", "sub A", image=_image())
        b = gallery.add_block("B", "sub B", image=_image())

        a.title = "Renamed"
        a.subtitle = None

        assert a.title == "Renamed"
        assert a.subtitle is None
        # Sibling is unaffected.
        assert b.title == "B"
        assert b.subtitle == "sub B"
    finally:
        server.stop()


def test_mutating_removed_block_is_a_noop() -> None:
    """A handle to a removed block must not resurrect it or raise.

    Click callbacks and user code can race a removal; silently ignoring the
    write keeps that safe.
    """
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery()
        a = gallery.add_block("A", image=_image())
        gallery.add_block("B", image=_image())
        a.remove()

        a.title = "ghost"
        a.subtitle = "ghost"
        a.set_image(_image())

        assert [blk.title for blk in gallery.blocks] == ["B"]
        assert a.title == ""
        assert a.subtitle is None
    finally:
        server.stop()


def test_remove_unknown_block_id_is_ignored() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery()
        gallery.add_block("A", image=_image())
        gallery.remove_block("does-not-exist")
        assert len(gallery.blocks) == 1
    finally:
        server.stop()


def test_add_block_requires_exactly_one_source() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery()
        with pytest.raises(ValueError, match="Exactly one"):
            gallery.add_block("neither")
        with pytest.raises(ValueError, match="Exactly one"):
            gallery.add_block("both", image=_image(), stl=b"solid\nendsolid\n")
    finally:
        server.stop()


def test_duplicate_block_id_rejected() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery()
        gallery.add_block("A", image=_image(), block_id="dup")
        with pytest.raises(ValueError, match="already contains"):
            gallery.add_block("B", image=_image(), block_id="dup")
    finally:
        server.stop()


def test_invalid_geometry_rejected() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        with pytest.raises(ValueError, match="must be positive"):
            server.gui.add_gallery(block_width=0)
        with pytest.raises(ValueError, match="must be positive"):
            server.gui.add_gallery(block_height=-1)
        with pytest.raises(ValueError, match="non-negative"):
            server.gui.add_gallery(gap=-1)
    finally:
        server.stop()


def test_placement_defaults_to_window() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        assert server.gui.add_gallery().placement == "window"
    finally:
        server.stop()


def test_placement_inline_for_containers() -> None:
    """A gallery given a container renders inline in it, not as an overlay."""
    server = viser.ViserServer(port=0, verbose=False)
    try:
        panel = server.gui.add_panel()
        tab = panel.add_tab("Parts")
        in_tab = server.gui.add_gallery(parent=tab)
        assert in_tab.placement == "inline"
        # Blocks work the same regardless of placement.
        in_tab.add_block("P", image=_image())
        assert len(in_tab.blocks) == 1

        folder = server.gui.add_folder("F")
        assert server.gui.add_gallery(parent=folder).placement == "inline"

        # `parent=None` picks up the ambient `with` context.
        with folder:
            assert server.gui.add_gallery(parent=None).placement == "inline"
    finally:
        server.stop()


def test_panel_as_parent_rejected() -> None:
    """A panel holds tabs, not content -- reject it with a pointer to add_tab."""
    server = viser.ViserServer(port=0, verbose=False)
    try:
        panel = server.gui.add_panel()
        with pytest.raises(TypeError, match="panel holds tabs"):
            server.gui.add_gallery(parent=panel)
    finally:
        server.stop()


def test_non_container_parent_rejected() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        other = server.gui.add_gallery()
        with pytest.raises(ValueError, match="not a GUI container"):
            server.gui.add_gallery(parent=other)  # type: ignore[arg-type]
    finally:
        server.stop()


def test_malformed_stl_raises() -> None:
    """Parse failures surface in Python rather than silently in the browser."""
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery()
        with pytest.raises(ValueError, match="Could not parse STL"):
            gallery.add_block("bad", stl=b"not an stl file at all")
        # The failed block was not left behind.
        assert gallery.blocks == ()
    finally:
        server.stop()


def test_stl_render_reply_populates_block() -> None:
    """A client render reply fills in the block's image and clears the wait."""
    server = viser.ViserServer(port=0, verbose=False)
    try:
        gallery = server.gui.add_gallery()
        block = gallery.add_block("A", image=_image())

        # Simulate the request/reply handshake without a browser.
        render_uuid = "render-1"
        gallery._pending_renders[render_uuid] = (
            block.block_id,
            threading.Event(),
        )
        gallery._handle_render_reply(render_uuid, b"\x89PNG-fake")

        assert gallery.blocks[0]._data == b"\x89PNG-fake"
        assert gallery.blocks[0]._format == "png"
        assert render_uuid not in gallery._pending_renders

        # A duplicate or unknown reply is ignored.
        gallery._handle_render_reply(render_uuid, b"other")
        assert gallery.blocks[0]._data == b"\x89PNG-fake"
    finally:
        server.stop()
