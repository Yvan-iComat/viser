"""Tests for the ``add_colorbar`` GUI component.

Covers the two things the server owns: canonicalizing the ramp to uint8 (the
same [0,255]-int / [0,1]-float convention as every other color in viser) and
generating tick labels whose precision follows the data range.
"""

from unittest.mock import patch

import numpy as np
import pytest

import viser
import viser._client_autobuild

RAMP = np.array([[0, 0, 0], [255, 255, 255]], dtype=np.uint8)


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_auto_ticks_scale_precision_to_the_range() -> None:
    """A 0-to-1 field needs decimals; a 0-to-500 field does not."""
    server = viser.ViserServer()
    try:
        unit = server.gui.add_colorbar(RAMP, vmin=0.0, vmax=1.0)
        assert [text for _, text in unit.ticks] == [
            "0.00",
            "0.25",
            "0.50",
            "0.75",
            "1.00",
        ]

        wide = server.gui.add_colorbar(RAMP, vmin=0.0, vmax=500.0, num_ticks=3)
        assert [text for _, text in wide.ticks] == ["0", "250", "500"]

        negative = server.gui.add_colorbar(RAMP, vmin=-20.0, vmax=120.0, num_ticks=3)
        assert [text for _, text in negative.ticks] == ["-20", "50", "120"]
        assert [value for value, _ in negative.ticks] == [-20.0, 50.0, 120.0]
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_tick_count_edge_cases() -> None:
    server = viser.ViserServer()
    try:
        assert server.gui.add_colorbar(RAMP, num_ticks=0).ticks == ()
        assert len(server.gui.add_colorbar(RAMP, num_ticks=1).ticks) == 1
        # A degenerate range has one distinct value, so one tick -- not
        # `num_ticks` duplicates stacked at the same spot.
        degenerate = server.gui.add_colorbar(RAMP, vmin=5.0, vmax=5.0, num_ticks=4)
        assert degenerate.ticks == ((5.0, "5.00"),)
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_explicit_ticks_override_num_ticks() -> None:
    server = viser.ViserServer()
    try:
        colorbar = server.gui.add_colorbar(
            RAMP, ticks=[(0.0, "cold"), (1.0, "hot")], num_ticks=99
        )
        assert colorbar.ticks == ((0.0, "cold"), (1.0, "hot"))
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_colors_follow_the_usual_int_float_convention() -> None:
    """Integers are [0,255] and floats are [0,1], as everywhere else in viser."""
    server = viser.ViserServer()
    try:
        from_int = server.gui.add_colorbar(np.array([[0, 128, 255], [255, 0, 0]]))
        from_float = server.gui.add_colorbar(
            np.array([[0.0, 128 / 255, 1.0], [1.0, 0.0, 0.0]])
        )
        assert from_int.colors.dtype == np.uint8
        assert from_float.colors.dtype == np.uint8
        np.testing.assert_array_equal(from_int.colors, from_float.colors)
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_props_are_assignable() -> None:
    """The handle is how a colorbar tracks a live colormap or range."""
    server = viser.ViserServer()
    try:
        colorbar = server.gui.add_colorbar(RAMP, vmin=0.0, vmax=1.0, label="T")
        colorbar.colors = np.array([[255, 0, 0], [0, 0, 255]], dtype=np.uint8)
        colorbar.vmax = 42.0
        colorbar.label = None
        np.testing.assert_array_equal(colorbar.colors[0], [255, 0, 0])
        assert colorbar.vmax == 42.0
        assert colorbar.label is None
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_ramp_shape_is_validated() -> None:
    server = viser.ViserServer()
    try:
        with pytest.raises(AssertionError, match="at least two control points"):
            server.gui.add_colorbar(np.zeros((1, 3), dtype=np.uint8))
        with pytest.raises(AssertionError, match=r"\(N, 3\)"):
            server.gui.add_colorbar(np.zeros((6,), dtype=np.uint8))
    finally:
        server.stop()
