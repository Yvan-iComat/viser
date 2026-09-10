"""Tests for the optional per-vertex color attribute on meshes.

`MeshProps.vertex_colors` is nullable, which makes it the first color array on
the wire whose type hint is `Optional[NDArray[uint8]]` rather than
`NDArray[uint8]`. The generic assignment path keys its color canonicalization
off the exact hint, so the nullable variant needs its own branch -- without it a
float array assigned while the current value is None reaches the wire as
float64, and one assigned over an existing uint8 array is truncated to zeros.
These pin that, plus the shape validation and the None round trip.
"""

from unittest.mock import patch

import numpy as np
import pytest

import viser
import viser._client_autobuild

VERTICES = np.array(
    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]]
)
FACES = np.array([[0, 1, 2], [1, 3, 2]])


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_vertex_colors_default_is_none() -> None:
    server = viser.ViserServer()
    try:
        mesh = server.scene.add_mesh_simple("/mesh", VERTICES, FACES)
        assert mesh.vertex_colors is None
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_vertex_colors_uint8_and_float_inputs_agree() -> None:
    """Integers are [0,255] and floats are [0,1], matching the `color` arg."""
    server = viser.ViserServer()
    try:
        from_int = server.scene.add_mesh_simple(
            "/int",
            VERTICES,
            FACES,
            vertex_colors=np.array([[0, 128, 255]] * 4, dtype=np.uint8),
        )
        from_float = server.scene.add_mesh_simple(
            "/float",
            VERTICES,
            FACES,
            vertex_colors=np.array([[0.0, 128 / 255, 1.0]] * 4),
        )
        assert from_int.vertex_colors is not None
        assert from_float.vertex_colors is not None
        assert from_int.vertex_colors.dtype == np.uint8
        assert from_float.vertex_colors.dtype == np.uint8
        np.testing.assert_array_equal(from_int.vertex_colors, from_float.vertex_colors)
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_vertex_colors_assignment_casts_from_none() -> None:
    """Assigning floats onto a mesh whose current value is None must still
    canonicalize to uint8 -- there is no existing dtype to coerce against."""
    server = viser.ViserServer()
    try:
        mesh = server.scene.add_mesh_simple("/mesh", VERTICES, FACES)
        assert mesh.vertex_colors is None

        mesh.vertex_colors = np.ones((4, 3))
        assert mesh.vertex_colors is not None
        assert mesh.vertex_colors.dtype == np.uint8
        np.testing.assert_array_equal(mesh.vertex_colors, np.full((4, 3), 255))

        # And over an existing uint8 array, floats must scale rather than
        # truncate to zero.
        mesh.vertex_colors = np.full((4, 3), 0.5)
        assert mesh.vertex_colors is not None
        assert mesh.vertex_colors.dtype == np.uint8
        np.testing.assert_array_equal(mesh.vertex_colors, np.full((4, 3), 127))
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_vertex_colors_can_be_cleared() -> None:
    server = viser.ViserServer()
    try:
        mesh = server.scene.add_mesh_simple(
            "/mesh", VERTICES, FACES, vertex_colors=np.zeros((4, 3), dtype=np.uint8)
        )
        assert mesh.vertex_colors is not None
        mesh.vertex_colors = None
        assert mesh.vertex_colors is None
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_vertex_colors_shape_is_validated() -> None:
    server = viser.ViserServer()
    try:
        with pytest.raises(AssertionError, match="vertex_colors"):
            server.scene.add_mesh_simple(
                "/mesh",
                VERTICES,
                FACES,
                # Three colors for four vertices.
                vertex_colors=np.zeros((3, 3), dtype=np.uint8),
            )
    finally:
        server.stop()


@patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None)
def test_skinned_mesh_inherits_vertex_colors() -> None:
    """`SkinnedMeshProps` subclasses `MeshProps`, so it gets the field for
    free; this checks the API and client actually wire it through."""
    server = viser.ViserServer()
    try:
        mesh = server.scene.add_mesh_skinned(
            "/skinned",
            VERTICES,
            FACES,
            bone_wxyzs=np.array([[1.0, 0.0, 0.0, 0.0]]),
            bone_positions=np.zeros((1, 3)),
            skin_weights=np.ones((4, 1)),
            vertex_colors=np.array([[10, 20, 30]] * 4, dtype=np.uint8),
        )
        assert mesh.vertex_colors is not None
        assert mesh.vertex_colors.dtype == np.uint8
        np.testing.assert_array_equal(mesh.vertex_colors[0], [10, 20, 30])
    finally:
        server.stop()
