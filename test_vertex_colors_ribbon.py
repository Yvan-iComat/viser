"""Per-vertex color test: a twisted ruled ribbon carrying a temperature field.

Builds a ruled surface (every cross-section is a straight segment) swept along a
helix, with the ruling direction twisting as it goes. A scalar "temperature"
runs from 0.0 at the start of the ribbon to 1.0 at the end, and is mapped through
a perceptually smooth colormap onto `add_mesh_simple(..., vertex_colors=...)`.

The "Vertex colors from" dropdown switches between three ways of getting from a
scalar field to vertex colors, which is the whole point of the script:

* **Analytic per-vertex.** The field is known at the vertices already. The
  rasterizer interpolates barycentrically across each triangle for free, and
  because adjacent faces share vertices the result is continuous across every
  edge. Smooth.
* **Per-face -> averaged.** The field is known per triangle instead (the usual
  case for simulation output). Area-weighted-average it onto the shared
  vertices first, and you get back the smooth result -- this is the "averaging
  at vertices" step.
* **Per-face flat.** The same per-triangle field applied without averaging: one
  constant color per triangle. A shared vertex cannot hold two different
  colors, so this mode has to duplicate every face corner into its own vertex,
  and the result is visibly faceted. This is the contrast case.

Note that duplicating vertices is not by itself what causes faceting. Unwelding
a per-*vertex* field gives duplicates at the same location the same color, so
the gradient stays smooth (only the shading normals go flat). Faceted color
comes specifically from a per-*face* value, which is what "Per-face flat" does.

Run with the fork on PYTHONPATH:

    PYTHONPATH=src python test_vertex_colors_ribbon.py
"""

from __future__ import annotations

import time

import numpy as np

import viser

# Colormap control points, sRGB 0-255, sampled at 17 evenly spaced positions
# from the reference perceptually-uniform ramps. Both are monotonic in
# lightness, and that monotonicity is what makes a gradient read as ordered.
# A rainbow (jet, hsv) is not monotonic, which is why it invents banding and
# false boundaries that are not in the data.
#
# 17 stops rather than a handful: piecewise-linear interpolation is only C0, so
# every stop is a slope discontinuity. Sparse stops put visible creases in a
# large smooth gradient; at this density the kinks fall below one 8-bit step.
COLORMAPS: dict[str, np.ndarray] = {
    # Inferno: reads as heat, dark = cold.
    "inferno": np.array(
        [
            [0, 0, 4],
            [11, 7, 36],
            [33, 12, 74],
            [61, 9, 101],
            [87, 16, 110],
            [113, 25, 110],
            [138, 34, 106],
            [163, 44, 97],
            [188, 55, 84],
            [210, 70, 68],
            [228, 90, 49],
            [241, 115, 29],
            [249, 142, 9],
            [252, 172, 17],
            [249, 203, 53],
            [242, 234, 105],
            [252, 255, 164],
        ],
        dtype=np.uint8,
    ),
    # Viridis: safer for color-vision deficiency, less "temperature"-looking.
    "viridis": np.array(
        [
            [68, 1, 84],
            [72, 24, 106],
            [71, 45, 123],
            [66, 64, 134],
            [59, 82, 139],
            [51, 99, 141],
            [44, 114, 142],
            [38, 130, 142],
            [33, 145, 140],
            [31, 160, 136],
            [40, 174, 128],
            [63, 188, 115],
            [94, 201, 98],
            [132, 212, 75],
            [173, 220, 48],
            [216, 226, 25],
            [253, 231, 37],
        ],
        dtype=np.uint8,
    ),
}


def colormap(scalars: np.ndarray, name: str) -> np.ndarray:
    """Map scalars in [0,1] to sRGB uint8.

    Interpolation happens in sRGB, not linear-light. That is deliberate and
    specific to this kind of table: these ramps were *built* to be perceptually
    uniform as sRGB values, so even steps in sRGB are even steps in perceived
    lightness. Converting to linear first and back would re-space the ramp and
    undo the property we picked it for. (Mixing two arbitrary brand colors is
    the opposite case -- there, interpolate in linear space.)
    """
    stops = COLORMAPS[name]
    positions = np.linspace(0.0, 1.0, len(stops))
    t = np.clip(scalars, 0.0, 1.0)
    rgb = np.stack(
        [np.interp(t, positions, stops[:, channel]) for channel in range(3)], axis=-1
    )
    return np.clip(rgb, 0, 255).astype(np.uint8)


def parallel_transport_frame(tangents: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotation-minimizing frame along a curve. Returns (normals, binormals).

    A Frenet frame would be shorter, but it spins through low-curvature
    stretches and flips sign at inflection points, which would put unintended
    twist into the ribbon. Parallel transport carries one arbitrary starting
    normal along the curve instead, so every turn of twist in the result is one
    we actually asked for.
    """
    num = len(tangents)
    normals = np.zeros((num, 3))

    # Seed with any vector not parallel to the first tangent.
    seed = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(seed, tangents[0])) > 0.9:
        seed = np.array([1.0, 0.0, 0.0])
    normals[0] = seed - np.dot(seed, tangents[0]) * tangents[0]
    normals[0] /= np.linalg.norm(normals[0])

    for i in range(1, num):
        # Rotate the previous normal by the rotation taking t[i-1] to t[i].
        axis = np.cross(tangents[i - 1], tangents[i])
        axis_norm = float(np.linalg.norm(axis))
        if axis_norm < 1e-12:
            normals[i] = normals[i - 1]
        else:
            axis = axis / axis_norm
            angle = np.arctan2(axis_norm, float(np.dot(tangents[i - 1], tangents[i])))
            # Rodrigues rotation formula.
            prev = normals[i - 1]
            normals[i] = (
                prev * np.cos(angle)
                + np.cross(axis, prev) * np.sin(angle)
                + axis * float(np.dot(axis, prev)) * (1.0 - np.cos(angle))
            )
        # Re-orthogonalize against the tangent to keep float drift bounded.
        normals[i] -= np.dot(normals[i], tangents[i]) * tangents[i]
        normals[i] /= np.linalg.norm(normals[i])

    return normals, np.cross(tangents, normals)


def build_ribbon(
    num_length: int, num_width: int, twists: float, width: float, turns: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a twisted ruled ribbon.

    Returns (vertices (V, 3), faces (F, 3), temperature (V,)), where the
    temperature is the normalized arc position of each vertex: 0.0 at the start
    of the ribbon, 1.0 at the end.

    Vertices are laid out as a (num_length x num_width) grid, and each interior
    one is SHARED by up to six triangles. Sharing is the whole point: colors are
    interpolated across a triangle's own corners, so a field defined on shared
    vertices comes out continuous across every edge.
    """
    u = np.linspace(0.0, 1.0, num_length)

    # Centerline: a helix, so "start" and "end" are visually unambiguous.
    angle = 2.0 * np.pi * turns * u
    centerline = np.stack(
        [np.cos(angle), np.sin(angle), 1.4 * u - 0.7],
        axis=-1,
    )

    # Unit tangents via central differences (one-sided at the ends).
    tangents = np.gradient(centerline, axis=0)
    tangents /= np.linalg.norm(tangents, axis=-1, keepdims=True)

    normals, binormals = parallel_transport_frame(tangents)

    # The ruling direction, rotating within the normal plane as we advance.
    # This is what makes the ribbon twisted rather than flat.
    theta = 2.0 * np.pi * twists * u
    ruling = np.cos(theta)[:, None] * normals + np.sin(theta)[:, None] * binormals

    # Grid: v in [-0.5, 0.5] across the ribbon, a straight segment at each u.
    v = np.linspace(-0.5, 0.5, num_width)
    vertices = (
        centerline[:, None, :] + (v * width)[None, :, None] * ruling[:, None, :]
    ).reshape(-1, 3)

    # Two triangles per quad, indexing into the flattened grid.
    i, j = np.meshgrid(
        np.arange(num_length - 1), np.arange(num_width - 1), indexing="ij"
    )
    lower_left = (i * num_width + j).ravel()
    lower_right = lower_left + 1
    upper_left = lower_left + num_width
    upper_right = upper_left + 1
    faces = np.concatenate(
        [
            np.stack([lower_left, lower_right, upper_right], axis=-1),
            np.stack([lower_left, upper_right, upper_left], axis=-1),
        ],
        axis=0,
    ).astype(np.uint32)

    # Temperature: 0.0 at the start, 1.0 at the end, constant across the width.
    # Broadcast the per-u value to every vertex in that row of the grid.
    temperature = np.repeat(u, num_width)
    return vertices, faces, temperature


def face_scalars_to_vertices(
    vertices: np.ndarray, faces: np.ndarray, face_scalars: np.ndarray
) -> np.ndarray:
    """Area-weighted average of a per-face field onto vertices.

    This is the averaging step needed when data lives on triangles (simulation
    cells, per-element results) rather than on vertices. Weighting by area
    rather than counting faces equally keeps the result from being pulled around
    by dense patches of small triangles.
    """
    corners = vertices[faces]
    areas = 0.5 * np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]),
        axis=-1,
    )

    weight_sum = np.zeros(len(vertices))
    value_sum = np.zeros(len(vertices))
    flat_indices = faces.ravel()
    np.add.at(weight_sum, flat_indices, np.repeat(areas, 3))
    np.add.at(value_sum, flat_indices, np.repeat(face_scalars * areas, 3))

    # Isolated vertices (no incident face) would divide by zero.
    return np.divide(
        value_sum, weight_sum, out=np.zeros_like(value_sum), where=weight_sum > 0
    )


def unweld_per_face(
    vertices: np.ndarray, faces: np.ndarray, face_scalars: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Render a per-face field flat: one constant color per triangle.

    A shared vertex can only carry one color, so a genuinely per-face field
    cannot be expressed on shared vertices at all -- every face corner has to
    become its own vertex, and all three corners of a face get that face's
    value. With no variation inside a triangle there is nothing to interpolate,
    so the surface comes out faceted. That is the visual cost of skipping the
    averaging step.
    """
    unwelded_vertices = vertices[faces].reshape(-1, 3)
    unwelded_scalars = np.repeat(face_scalars, 3)
    unwelded_faces = np.arange(len(unwelded_vertices), dtype=np.uint32).reshape(-1, 3)
    return unwelded_vertices, unwelded_faces, unwelded_scalars


def main() -> None:
    server = viser.ViserServer()
    server.scene.set_up_direction("+z")
    server.scene.add_grid("/grid", width=4.0, height=4.0, position=(0.0, 0.0, -0.9))

    with server.gui.add_folder("Ribbon"):
        gui_twists = server.gui.add_slider("Twists", 0.0, 6.0, 0.25, 2.0)
        gui_turns = server.gui.add_slider("Helix turns", 0.5, 3.0, 0.25, 1.5)
        gui_width = server.gui.add_slider("Width", 0.05, 0.8, 0.01, 0.35)
        # Deliberately not maxed out. The smooth gradient does not need
        # resolution -- interpolation is continuous, so "Analytic per-vertex"
        # looks identical at 80 samples and at 600. But the contrast modes DO:
        # at 300 samples "Per-face flat" produces ~300 colour bands across a
        # ribbon a few hundred pixels long, i.e. about one band per pixel, and
        # flat shading's facets are sub-pixel too -- so both controls look
        # broken. At 80 the bands are ~12/255 apart and plainly visible.
        gui_length_res = server.gui.add_slider("Length samples", 8, 600, 1, 60)
        gui_width_res = server.gui.add_slider("Width samples", 2, 40, 1, 5)

    with server.gui.add_folder("Temperature field"):
        gui_colormap = server.gui.add_dropdown(
            "Colormap", tuple(COLORMAPS.keys()), initial_value="inferno"
        )
        gui_mode = server.gui.add_dropdown(
            "Vertex colors from",
            (
                "Analytic per-vertex",
                "Per-face -> averaged",
                "Per-face flat",
            ),
            initial_value="Analytic per-vertex",
            hint=(
                "The difference is resolution-dependent: raise 'Length "
                "samples' and 'Per-face flat' converges to look identical to "
                "the smooth modes, because its bands shrink below a pixel."
            ),
        )
        gui_smooth = server.gui.add_checkbox(
            "Smooth shading",
            True,
            hint=(
                "Averages normals across shared vertices. Affects shading "
                "only, never the colors. Like the mode above, it is only "
                "visible when triangles are big enough to see."
            ),
        )
        gui_animate = server.gui.add_checkbox("Animate (scroll field)", False)

    gui_stats = server.gui.add_markdown("")

    # The colorbar gets its own movable window rather than a folder in the
    # control panel: it is a legend for what is on screen, so it should be
    # placeable next to the thing it explains. Floated left so it does not
    # land under the control panel, which floats top-right by default.
    colorbar_panel = server.gui.add_panel()
    with colorbar_panel.add_tab("Colorbar", viser.Icon.TEMPERATURE):
        gui_colorbar = server.gui.add_colorbar(
            COLORMAPS["inferno"],
            vmin=0.0,
            vmax=1.0,
            label="Temperature",
        )
    colorbar_panel.float(x=15, y=15, width=170, height=330)

    handle: viser.MeshHandle | None = None
    temperature: np.ndarray | None = None

    def rebuild() -> None:
        nonlocal handle, temperature

        vertices, faces, field = build_ribbon(
            num_length=int(gui_length_res.value),
            num_width=int(gui_width_res.value),
            twists=float(gui_twists.value),
            width=float(gui_width.value),
            turns=float(gui_turns.value),
        )

        if gui_mode.value != "Analytic per-vertex":
            # Both per-face modes start the same way: sample the field at each
            # triangle's centroid, i.e. throw the per-vertex values away. They
            # differ only in whether the averaging step is done.
            face_field = field[faces].mean(axis=1)
            if gui_mode.value == "Per-face -> averaged":
                field = face_scalars_to_vertices(vertices, faces, face_field)
            else:
                vertices, faces, field = unweld_per_face(vertices, faces, face_field)

        # The one line this whole script exists to exercise.
        handle = server.scene.add_mesh_simple(
            "/ribbon",
            vertices=vertices,
            faces=faces,
            vertex_colors=colormap(field, gui_colormap.value),
            smooth_shading=bool(gui_smooth.value),
            side="double",  # A ribbon is thin; show both faces.
        )
        temperature = field

        gui_stats.content = (
            f"**{len(vertices):,} vertices, {len(faces):,} faces**\n\n"
            f"Temperature {field.min():.2f} to {field.max():.2f}"
        )

        # Only the ramp changes; `vmin`/`vmax` stay at the colormap's domain of
        # [0, 1] because that is the mapping the bar describes. Labeling it with
        # the field's own min/max would be wrong whenever the two differ:
        # "Per-face -> averaged" compresses the extremes slightly, and its
        # ribbon genuinely does not reach the ends of the ramp. The stats line
        # above reports that actual extent separately.
        gui_colorbar.colors = COLORMAPS[gui_colormap.value]

    for gui_input in (
        gui_twists,
        gui_turns,
        gui_width,
        gui_length_res,
        gui_width_res,
        gui_colormap,
        gui_mode,
        gui_smooth,
    ):
        gui_input.on_update(lambda _: rebuild())

    rebuild()
    print("Ribbon ready. Open the URL above.")

    # Animating the field exercises the streaming path: only the color buffer is
    # re-sent and re-uploaded, the geometry is left alone.
    while True:
        if gui_animate.value and handle is not None and temperature is not None:
            scrolled = (temperature + time.time() * 0.25) % 1.0
            handle.vertex_colors = colormap(scrolled, gui_colormap.value)
            time.sleep(1.0 / 60.0)
        else:
            time.sleep(0.1)


if __name__ == "__main__":
    main()
