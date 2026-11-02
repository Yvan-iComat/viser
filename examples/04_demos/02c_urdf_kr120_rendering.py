"""URDF rendering options

Test bench for realistic rendering of a URDF: shading (flat, smooth, smooth
with creases), feature-edge overlay, explicit PBR materials per link,
environment lighting and shadows. Defaults to the KR120 from
``02b_urdf_visualizer_kr120.py``; pass ``--urdf-path`` for another robot, e.g.
the STL-only ``winder_urdf/winder_robot.urdf``.

Mesh formats differ in what material data they carry. The KR120's COLLADA
files have plain ``<lambert>`` materials, which trimesh loads as
``roughness=1, metallic=0`` (fully matte); STL files carry no material at all
(only an optional URDF ``<color>``). :class:`viser.extras.MeshMaterial` sets the
material explicitly; fields left unset keep the file's values.

The "Materials" panel edits the same ``materials`` mapping that is passed to
:class:`viser.extras.ViserUrdf`, keyed by ``"*"`` (all meshes) or link name.
"Print materials" writes it out as Python to paste into your own code.

**Features:**

* :class:`viser.extras.ViserUrdf` ``smooth_shading`` / ``crease_angle``
* :class:`viser.extras.MeshMaterial` overrides per link
* :meth:`viser.SceneApi.configure_environment_map` and default lights
* Shadow-catching ground plane (``receive_shadow`` as a float)
"""

from __future__ import annotations

import time
from functools import partial
from pathlib import Path

import numpy as np
import tyro
import yourdfpy

import viser
from viser.extras import MeshMaterial, ViserUrdf

DEFAULT_URDF_PATH = Path(__file__).parent / "kr120" / "kr120_r2700_2.urdf"

HDRI_PRESETS = (
    "none",
    "apartment",
    "city",
    "dawn",
    "forest",
    "lobby",
    "night",
    "park",
    "studio",
    "sunset",
    "warehouse",
)
SHADING_MODES = ("flat", "smooth (all edges)", "smooth + creases")
ALL_MESHES = "*"


def _format_materials(materials: dict[str, MeshMaterial]) -> str:
    lines = ["materials = {"]
    for key, material in materials.items():
        fields = ", ".join(
            f"{name}={value!r}"
            for name, value in vars(material).items()
            if value is not None
        )
        lines.append(f"    {key!r}: MeshMaterial({fields}),")
    lines.append("}")
    return "\n".join(lines)


def main(
    urdf_path: Path = DEFAULT_URDF_PATH, scale: float = 1.0, port: int = 8081
) -> None:
    server = viser.ViserServer(port=port)
    server.gui.configure_theme(dark_mode=False, control_width="large")

    urdf = yourdfpy.URDF.load(
        urdf_path,
        build_scene_graph=True,
        load_meshes=True,
        filename_handler=partial(yourdfpy.filename_handler_magic, dir=urdf_path.parent),
    )
    assert urdf.scene is not None
    lo, hi = urdf.scene.bounds * scale
    center = (lo + hi) / 2.0
    extent = float(np.linalg.norm(hi - lo))
    server.initial_camera.position = tuple(
        center + extent * np.array([0.6, -0.75, 0.45])
    )
    server.initial_camera.look_at = tuple(center)
    server.scene.add_frame("/robot", show_axes=False)

    # Links that carry visual geometry.
    visual_links = sorted(
        {urdf.scene.graph.transforms.parents[name] for name in urdf.scene.geometry}
    )
    materials: dict[str, MeshMaterial] = {ALL_MESHES: MeshMaterial(roughness=0.4)}

    with server.gui.add_folder("Shading"):
        shading = server.gui.add_dropdown(
            "Mode", SHADING_MODES, initial_value=SHADING_MODES[2]
        )
        crease_deg = server.gui.add_slider(
            "Crease angle (deg)", min=5.0, max=90.0, step=1.0, initial_value=30.0
        )
        feature_edges = server.gui.add_checkbox("Feature edges", False)
        apply_shading = server.gui.add_button("Apply crease angle")
    with server.gui.add_folder("Materials"):
        target = server.gui.add_dropdown(
            "Link", (ALL_MESHES, *visual_links), initial_value=ALL_MESHES
        )
        set_color = server.gui.add_checkbox("Override color", False)
        color = server.gui.add_rgb("Color", initial_value=(255, 102, 0))
        set_roughness = server.gui.add_checkbox("Override roughness", True)
        roughness = server.gui.add_slider(
            "Roughness", min=0.0, max=1.0, step=0.01, initial_value=0.4
        )
        set_metalness = server.gui.add_checkbox("Override metalness", False)
        metalness = server.gui.add_slider(
            "Metalness", min=0.0, max=1.0, step=0.01, initial_value=0.0
        )
        opacity = server.gui.add_slider(
            "Opacity", min=0.05, max=1.0, step=0.01, initial_value=1.0
        )
        set_button = server.gui.add_button("Set for link")
        clear_button = server.gui.add_button("Clear link")
        print_button = server.gui.add_button("Print materials")
    with server.gui.add_folder("Lighting"):
        hdri = server.gui.add_dropdown(
            "Environment", HDRI_PRESETS, initial_value="studio"
        )
        env_intensity = server.gui.add_slider(
            "Env intensity", min=0.0, max=3.0, step=0.05, initial_value=1.0
        )
        background = server.gui.add_checkbox("Show env background", False)
        default_lights = server.gui.add_checkbox("Default lights", True)
        shadows = server.gui.add_checkbox("Shadows", True)
        ground = server.gui.add_checkbox("Shadow ground", True)
    status = server.gui.add_markdown("")

    viser_urdf: ViserUrdf | None = None

    def rebuild() -> None:
        nonlocal viser_urdf
        cfg = urdf.cfg
        if viser_urdf is not None:
            viser_urdf.remove()
        start = time.perf_counter()
        viser_urdf = ViserUrdf(
            server,
            urdf,
            scale=scale,
            root_node_name="/robot",
            smooth_shading=shading.value != "flat",
            crease_angle=(
                np.deg2rad(crease_deg.value)
                if shading.value == "smooth + creases"
                else None
            ),
            show_feature_edges=feature_edges.value,
            materials=dict(materials),
        )
        # Joint frames are only placed by update_cfg().
        viser_urdf.update_cfg(cfg)
        status.content = (
            f"Rebuilt `{shading.value}` in {time.perf_counter() - start:.2f} s"
        )

    def load_target(_) -> None:
        """Show the selected link's current entry in the controls."""
        material = materials.get(target.value, MeshMaterial())
        set_color.value = material.color is not None
        if material.color is not None:
            color.value = tuple(int(c) for c in material.color)  # type: ignore[assignment]
        set_roughness.value = material.roughness is not None
        roughness.value = 1.0 if material.roughness is None else material.roughness
        set_metalness.value = material.metalness is not None
        metalness.value = 0.0 if material.metalness is None else material.metalness
        opacity.value = 1.0 if material.opacity is None else material.opacity

    @set_button.on_click
    def _(_) -> None:
        materials[target.value] = MeshMaterial(
            color=color.value if set_color.value else None,
            roughness=roughness.value if set_roughness.value else None,
            metalness=metalness.value if set_metalness.value else None,
            opacity=opacity.value if opacity.value < 1.0 else None,
        )
        rebuild()

    @clear_button.on_click
    def _(_) -> None:
        if materials.pop(target.value, None) is not None:
            rebuild()
        load_target(None)

    @print_button.on_click
    def _(_) -> None:
        text = _format_materials(materials)
        print(text)
        status.content = f"```python\n{text}\n```"

    def update_lighting() -> None:
        server.scene.configure_environment_map(
            None if hdri.value == "none" else hdri.value,
            background=background.value,
            background_blurriness=0.6,
            environment_intensity=env_intensity.value,
        )
        server.scene.configure_default_lights(
            enabled=default_lights.value, cast_shadow=shadows.value
        )
        ground_handle.visible = ground.value

    target.on_update(load_target)
    apply_shading.on_click(lambda _: rebuild())
    for handle in (shading, feature_edges):
        handle.on_update(lambda _: rebuild())
    for handle in (hdri, env_intensity, background, default_lights, shadows, ground):
        handle.on_update(lambda _: update_lighting())

    ground_handle = server.scene.add_box(
        "/ground",
        color=(230, 230, 230),
        dimensions=(4.0 * extent, 4.0 * extent, 0.002 * extent),
        position=(center[0], center[1], lo[2] - 0.001 * extent),
        # A float draws the shadow at a fixed opacity, independent of lighting.
        receive_shadow=0.4,
        cast_shadow=False,
    )
    load_target(None)
    update_lighting()
    rebuild()

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    tyro.cli(main)
