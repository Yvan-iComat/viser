"""Tests for crease-aware smooth normals (`viser._mesh_normals`)."""

from __future__ import annotations

import io

import numpy as np
import trimesh

import viser
from viser._mesh_normals import split_vertices_at_creases


def _cylinder() -> trimesh.Trimesh:
    return trimesh.creation.cylinder(radius=1.0, height=2.0, sections=32)


def test_box_splits_every_corner_into_three() -> None:
    box = trimesh.creation.box()
    out = split_vertices_at_creases(box.vertices, box.faces, np.deg2rad(30))
    assert out.vertices.shape == (24, 3)
    assert out.feature_edges.shape == (12, 2)
    # Each split vertex takes the normal of its own (axis-aligned) side.
    assert np.allclose(np.abs(out.normals).max(axis=1), 1.0)


def test_no_crease_keeps_shared_vertices() -> None:
    box = trimesh.creation.box()
    out = split_vertices_at_creases(box.vertices, box.faces, None)
    assert out.vertices.shape == (8, 3)
    assert len(out.feature_edges) == 0


def test_cylinder_side_smooth_caps_sharp() -> None:
    cyl = _cylinder()
    out = split_vertices_at_creases(cyl.vertices, cyl.faces, np.deg2rad(30))
    # 32 rim vertices per cap, duplicated for side/cap, plus 2 cap centers.
    assert out.vertices.shape == (130, 3)
    # Side normals are horizontal and point outward.
    side = np.abs(out.normals[:, 2]) < 1e-6
    assert side.sum() == 64
    radial = out.vertices[side, :2] / np.linalg.norm(
        out.vertices[side, :2], axis=1, keepdims=True
    )
    assert np.allclose(out.normals[side, :2], radial, atol=1e-5)
    # Cap normals are vertical.
    assert np.allclose(np.abs(out.normals[~side, 2]), 1.0)


def test_triangle_soup_is_rewelded() -> None:
    cyl = _cylinder()
    soup_vertices = cyl.triangles.reshape(-1, 3)
    soup_faces = np.arange(len(soup_vertices)).reshape(-1, 3)
    out = split_vertices_at_creases(soup_vertices, soup_faces, np.deg2rad(30))
    assert out.vertices.shape == (130, 3)
    kept = split_vertices_at_creases(
        soup_vertices,
        soup_faces,
        np.deg2rad(30),
        preserve_vertex_identity=True,
    )
    assert kept.vertices.shape == soup_vertices.shape


def test_user_sharp_edge_splits_where_it_separates_a_fan() -> None:
    cyl = _cylinder()
    cyl.merge_vertices()
    edges = cyl.face_adjacency_edges
    seg = cyl.vertices[edges]
    vertical = edges[np.argmax(np.abs(seg[:, 0, 2] - seg[:, 1, 2]))]
    out = split_vertices_at_creases(
        cyl.vertices, cyl.faces, np.deg2rad(30), sharp_edges=vertical[None]
    )
    # A rim-to-rim seam splits both of its end vertices.
    assert out.vertices.shape == (132, 3)
    assert out.feature_edges.shape == (65, 2)
    # Alone, a single sharp edge inside a closed smooth fan cannot split it.
    alone = split_vertices_at_creases(
        cyl.vertices, cyl.faces, None, sharp_edges=vertical[None]
    )
    assert alone.vertices.shape == cyl.vertices.shape


def test_folded_sheet_beyond_90_degrees_is_sharp() -> None:
    # Two triangles folded back onto each other, 10 degrees apart: their
    # normals differ by 170 degrees. (arccos(|dot|) would report 10.)
    theta = np.deg2rad(10)
    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, np.cos(theta), np.sin(theta)]]
    )
    faces = np.array([[0, 1, 2], [1, 0, 3]])
    out = split_vertices_at_creases(vertices, faces, np.deg2rad(30))
    assert out.vertices.shape == (6, 3)
    assert out.feature_edges.shape == (1, 2)
    # The nearly-flat hinge (normals 10 degrees apart) stays smooth.
    flat = vertices.copy()
    flat[3] = [0, np.cos(np.deg2rad(170)), np.sin(np.deg2rad(170))]
    assert split_vertices_at_creases(flat, faces, np.deg2rad(30)).vertices.shape == (
        4,
        3,
    )


def test_add_mesh_simple_sends_split_topology() -> None:
    server = viser.ViserServer(port=0)
    try:
        box = trimesh.creation.box()
        handle = server.scene.add_mesh_simple(
            "/box",
            box.vertices,
            box.faces,
            vertex_colors=np.tile([255, 0, 0], (8, 1)),
            smooth_shading=True,
            crease_angle=np.deg2rad(30),
        )
        assert handle.vertices.shape == (24, 3)
        assert handle.vertex_colors is not None
        assert handle.vertex_colors.shape == (24, 3)
        plain = server.scene.add_mesh_simple(
            "/plain", box.vertices, box.faces, smooth_shading=True
        )
        assert plain.vertices.shape == (8, 3)
    finally:
        server.stop()


def test_add_mesh_trimesh_exports_split_normals() -> None:
    server = viser.ViserServer(port=0)
    try:
        box = trimesh.creation.box()
        handle = server.scene.add_mesh_trimesh(
            "/box", box, smooth_shading=True, crease_angle=np.deg2rad(30)
        )
        loaded = trimesh.load(
            io.BytesIO(handle.glb_data), file_type="glb", force="mesh", process=False
        )
        assert isinstance(loaded, trimesh.Trimesh)
        assert len(loaded.vertices) == 24
        assert np.allclose(np.abs(loaded.vertex_normals).max(axis=1), 1.0)
    finally:
        server.stop()
