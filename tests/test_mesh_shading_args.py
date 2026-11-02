"""`add_mesh_simple` shading arguments: `smooth_shading` wins over the
deprecated `flat_shading`, which must still be honored on its own."""

from __future__ import annotations

import warnings

import pytest
import trimesh

import viser


@pytest.fixture
def server():
    server = viser.ViserServer(port=0)
    yield server
    server.stop()


@pytest.mark.parametrize(
    ("kwargs", "expected_flat"),
    [
        ({}, True),
        ({"flat_shading": False}, False),
        ({"flat_shading": True}, True),
        ({"smooth_shading": True}, False),
        ({"smooth_shading": False}, True),
        ({"flat_shading": True, "smooth_shading": False}, True),
        ({"flat_shading": False, "smooth_shading": True}, False),
    ],
)
def test_shading_resolution(
    server: viser.ViserServer, kwargs: dict, expected_flat: bool
) -> None:
    box = trimesh.creation.box()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        handle = server.scene.add_mesh_simple("/m", box.vertices, box.faces, **kwargs)
    assert handle.flat_shading is expected_flat


@pytest.mark.parametrize(
    ("flat", "smooth", "expected_flat"), [(True, True, False), (False, False, True)]
)
def test_conflicting_arguments_warn_and_smooth_wins(
    server: viser.ViserServer, flat: bool, smooth: bool, expected_flat: bool
) -> None:
    box = trimesh.creation.box()
    with pytest.warns(UserWarning, match="smooth_shading takes precedence"):
        handle = server.scene.add_mesh_simple(
            "/m", box.vertices, box.faces, flat_shading=flat, smooth_shading=smooth
        )
    assert handle.flat_shading is expected_flat


def test_wireframe_warns_only_for_explicit_flat(server: viser.ViserServer) -> None:
    box = trimesh.creation.box()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        server.scene.add_mesh_simple("/a", box.vertices, box.faces, wireframe=True)
    with pytest.warns(UserWarning, match="Flat shading argument will be ignored"):
        server.scene.add_mesh_simple(
            "/b", box.vertices, box.faces, wireframe=True, flat_shading=True
        )
