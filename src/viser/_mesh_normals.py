"""Crease-aware smooth normals.

Averaging vertex normals over every face that touches a vertex makes hard
edges (box corners, flanges, chamfers) look smeared. Here, the faces around
each vertex are split into smoothing groups that are connected only across
*smooth* edges; each group gets its own copy of the vertex and its own normal.
An edge is sharp when the angle between its two face normals exceeds a crease
angle, when it is listed explicitly by the caller, or when it is a boundary or
non-manifold edge.

Because the output topology already encodes the creases, a renderer that
recomputes per-vertex normals from the index buffer (as the viser client does)
reproduces the same shading.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import numpy.typing as npt


@dataclasses.dataclass(frozen=True)
class CreaseSplitResult:
    vertices: npt.NDArray[np.float32]
    """(V', 3) vertex positions, duplicated along sharp edges."""
    faces: npt.NDArray[np.uint32]
    """(F, 3) faces indexing into `vertices`. Face order is preserved."""
    normals: npt.NDArray[np.float32]
    """(V', 3) unit vertex normals, angle-weighted within each smoothing group."""
    source_index: npt.NDArray[np.int64]
    """(V',) index of the input vertex each output vertex was copied from. Use
    it to carry per-vertex attributes (colors, UVs) over to the output."""
    feature_edges: npt.NDArray[np.int64]
    """(K, 2) interior edges that were treated as sharp (crease angle,
    user-listed, or non-manifold), as pairs of input vertex indices. Boundary
    edges are not included."""


def _union_pairs(n: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Connected-component labels for `n` nodes joined by edges (u, v).

    Min-label propagation with pointer jumping. Every component here is the
    fan of corners around a single vertex, so this converges in a handful of
    iterations even for large meshes."""
    labels = np.arange(n, dtype=np.int64)
    if len(u) == 0:
        return labels
    while True:
        m = np.minimum(labels[u], labels[v])
        if np.all(labels[u] == m) and np.all(labels[v] == m):
            return labels
        np.minimum.at(labels, u, m)
        np.minimum.at(labels, v, m)
        while True:
            jumped = labels[labels]
            if np.array_equal(jumped, labels):
                break
            labels = jumped


def split_vertices_at_creases(
    vertices: np.ndarray,
    faces: np.ndarray,
    crease_angle: float | None,
    sharp_edges: np.ndarray | None = None,
    *,
    preserve_vertex_identity: bool = False,
    weld_tolerance: float = 1e-6,
) -> CreaseSplitResult:
    """Split vertices along sharp edges so smooth normals stop at creases.

    Args:
        vertices: (V, 3) vertex positions. Vertices that coincide (within
            `weld_tolerance`) are treated as connected, so STL-style triangle
            soups are handled.
        faces: (F, 3) triangle vertex indices.
        crease_angle: Edges whose adjacent face normals differ by more than
            this angle (radians) are sharp. None disables angle-based creases.
        sharp_edges: Optional (E, 2) pairs of vertex indices whose edges are
            always sharp, regardless of angle.
        preserve_vertex_identity: If True, distinct input vertices are never
            merged, even when they coincide and share a smoothing group (they
            still get the same normal). Use this to keep UV seams. If False,
            coincident vertices in the same smoothing group are merged, which
            is what an index-based normal recomputation needs.
        weld_tolerance: Distance below which vertices are considered
            coincident when building connectivity.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    assert vertices.ndim == 2 and vertices.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 3
    num_faces = faces.shape[0]
    num_corners = 3 * num_faces

    # Weld coincident positions so connectivity ignores per-face duplicates.
    quantized = np.round(vertices / weld_tolerance).astype(np.int64)
    _, weld_first, weld = np.unique(
        quantized, axis=0, return_index=True, return_inverse=True
    )
    weld = weld.reshape(-1)
    num_welded = weld_first.shape[0]
    faces_welded = weld[faces]

    # Unit face normals and per-corner interior angles (normal weights).
    tri = vertices[faces]
    face_normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(face_normals, axis=1, keepdims=True)
    face_normals = np.divide(
        face_normals, norm, out=np.zeros_like(face_normals), where=norm > 0
    )
    corner_angles = np.empty((num_faces, 3))
    for k in range(3):
        a = tri[:, (k + 1) % 3] - tri[:, k]
        b = tri[:, (k + 2) % 3] - tri[:, k]
        la = np.linalg.norm(a, axis=1)
        lb = np.linalg.norm(b, axis=1)
        denom = la * lb
        cos = np.divide(
            np.einsum("ij,ij->i", a, b),
            denom,
            out=np.ones_like(denom),
            where=denom > 0,
        )
        corner_angles[:, k] = np.arccos(np.clip(cos, -1.0, 1.0))

    # Half-edges: half-edge 3f+k runs from corner k to corner (k+1)%3 of face f.
    corner_ids = np.arange(num_corners, dtype=np.int64).reshape(num_faces, 3)
    he_corner_a = corner_ids.reshape(-1)
    he_corner_b = corner_ids[:, [1, 2, 0]].reshape(-1)
    he_va = faces_welded.reshape(-1)
    he_vb = faces_welded[:, [1, 2, 0]].reshape(-1)
    he_face = np.repeat(np.arange(num_faces, dtype=np.int64), 3)
    lo = np.minimum(he_va, he_vb)
    hi = np.maximum(he_va, he_vb)
    he_key = lo * num_welded + hi

    # Drop half-edges of degenerate (collapsed-edge) triangles.
    valid = lo != hi
    he_ids = np.nonzero(valid)[0]
    order = he_ids[np.argsort(he_key[he_ids], kind="stable")]
    sorted_keys = he_key[order]
    run_starts = np.concatenate(
        [[0], np.nonzero(np.diff(sorted_keys))[0] + 1, [len(order)]]
    )
    run_lengths = np.diff(run_starts)

    # Manifold interior edges: exactly two half-edges.
    pair_starts = run_starts[:-1][run_lengths == 2]
    h1 = order[pair_starts]
    h2 = order[pair_starts + 1]
    cos_threshold = -np.inf if crease_angle is None else np.cos(crease_angle)
    dots = np.einsum("ij,ij->i", face_normals[he_face[h1]], face_normals[he_face[h2]])
    sharp = dots < cos_threshold

    if sharp_edges is not None:
        sharp_edges = np.asarray(sharp_edges, dtype=np.int64).reshape(-1, 2)
        sw = weld[sharp_edges]
        user_keys = np.minimum(sw[:, 0], sw[:, 1]) * num_welded + np.maximum(
            sw[:, 0], sw[:, 1]
        )
        sharp |= np.isin(he_key[h1], user_keys)

    # Join the corners on either side of each smooth edge. Half-edges of two
    # consistently-wound faces run in opposite directions.
    smooth = ~sharp
    s1, s2 = h1[smooth], h2[smooth]
    same_dir = he_va[s1] == he_va[s2]
    join_u = np.concatenate([he_corner_a[s1], he_corner_b[s1]])
    join_v = np.concatenate(
        [
            np.where(same_dir, he_corner_a[s2], he_corner_b[s2]),
            np.where(same_dir, he_corner_b[s2], he_corner_a[s2]),
        ]
    )
    group = _union_pairs(num_corners, join_u, join_v)

    # Angle-weighted normal per smoothing group.
    weighted = (face_normals[:, None, :] * corner_angles[:, :, None]).reshape(-1, 3)
    group_normals = np.zeros((num_corners, 3))
    np.add.at(group_normals, group, weighted)
    gn = np.linalg.norm(group_normals, axis=1, keepdims=True)
    group_normals = np.divide(
        group_normals,
        gn,
        out=np.tile(np.array([0.0, 0.0, 1.0]), (num_corners, 1)),
        where=gn > 0,
    )

    # One output vertex per (vertex, smoothing group).
    corner_vertex = faces.reshape(-1)
    identity = corner_vertex if preserve_vertex_identity else weld[corner_vertex]
    out_key = identity * num_corners + group
    _, first, inverse = np.unique(out_key, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    source_index = corner_vertex[first]

    # Feature edges: sharp manifold edges plus non-manifold edges.
    nonmanifold_starts = run_starts[:-1][run_lengths > 2]
    feature_he = np.concatenate([h1[sharp], order[nonmanifold_starts]])
    feature_edges = weld_first[np.stack([he_va[feature_he], he_vb[feature_he]], axis=1)]

    return CreaseSplitResult(
        vertices=vertices[source_index].astype(np.float32),
        faces=inverse.reshape(num_faces, 3).astype(np.uint32),
        normals=group_normals[group[first]].astype(np.float32),
        source_index=source_index,
        feature_edges=feature_edges.astype(np.int64),
    )
