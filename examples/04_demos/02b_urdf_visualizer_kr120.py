"""KUKA KR120 R2700-2 URDF visualizer

Adapted from ``02_urdf_visualizer.py`` to load a local URDF from disk instead of
the ``robot_descriptions`` library.

The robot ships as ``kr120/kr120_r2700_2.urdf.xacro``, which cannot be loaded
directly: it is only a thin wrapper that pulls the actual links and joints from
``$(find kuka_quantec_support)/urdf/kr120_r2700_2_macro.xacro``, and that ROS
package is not vendored here. ``kr120/kr120_r2700_2.urdf`` is a plain-URDF
equivalent that reproduces the KR120 R2700-2 kinematics against the supplied
meshes.

Meshes are COLLADA (``.dae``) rather than STL. trimesh loads both, and the
``package://`` URIs resolve because :class:`viser.extras.ViserUrdf` uses
yourdfpy's ``filename_handler_magic`` relative to the URDF's directory.

**Features:**

* :class:`viser.extras.ViserUrdf` loading a local URDF path
* Interactive joint sliders for robot articulation
* Real-time robot pose updates
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import tyro

import viser
from viser.extras import ViserUrdf

# Default to the KR120 URDF that sits next to this script.
DEFAULT_URDF_PATH = Path(__file__).parent / "kr120" / "kr120_r2700_2.urdf"


def create_robot_control_sliders(
    server: viser.ViserServer, viser_urdf: ViserUrdf
) -> tuple[list[viser.GuiInputHandle[float]], list[float]]:
    """Create slider for each joint of the robot. We also update robot model
    when slider moves."""
    slider_handles: list[viser.GuiInputHandle[float]] = []
    initial_config: list[float] = []
    for joint_name, (
        lower,
        upper,
    ) in viser_urdf.get_actuated_joint_limits().items():
        lower = lower if lower is not None else -np.pi
        upper = upper if upper is not None else np.pi
        initial_pos = 0.0 if lower < -0.1 and upper > 0.1 else (lower + upper) / 2.0
        slider = server.gui.add_slider(
            label=joint_name,
            min=lower,
            max=upper,
            step=1e-3,
            initial_value=initial_pos,
        )
        slider.on_update(  # When sliders move, we update the URDF configuration.
            lambda _: viser_urdf.update_cfg(
                np.array([slider.value for slider in slider_handles])
            )
        )
        slider_handles.append(slider)
        initial_config.append(initial_pos)
    return slider_handles, initial_config


def main(
    urdf_path: Path = DEFAULT_URDF_PATH,
    load_meshes: bool = True,
    load_collision_meshes: bool = False,
) -> None:
    if not urdf_path.exists():
        raise FileNotFoundError(f"URDF not found: {urdf_path}")

    # Start viser server.
    server = viser.ViserServer()
    server.gui.configure_theme(dark_mode=False, control_width="large")

    # The KR120 has a ~2.7m reach, so pull the camera back further than the
    # panda-sized default in the original example.
    server.initial_camera.position = (4.0, 4.0, 3.0)
    server.initial_camera.look_at = (0.0, 0.0, 1.0)

    # Load URDF. ViserUrdf takes either a yourdfpy.URDF object or a path to a
    # .urdf file; passing the path lets it resolve the package:// mesh URIs
    # relative to the URDF's own directory.
    viser_urdf = ViserUrdf(
        server,
        urdf_or_path=urdf_path,
        load_meshes=load_meshes,
        load_collision_meshes=load_collision_meshes,
        collision_mesh_color_override=(1.0, 0.0, 0.0, 0.5),
    )

    # Create sliders in GUI that help us move the robot joints.
    with server.gui.add_folder("Joint position control"):
        (slider_handles, initial_config) = create_robot_control_sliders(
            server, viser_urdf
        )

    # Add visibility checkboxes.
    with server.gui.add_folder("Visibility"):
        show_meshes_cb = server.gui.add_checkbox(
            "Show meshes",
            viser_urdf.show_visual,
        )
        show_collision_meshes_cb = server.gui.add_checkbox(
            "Show collision meshes", viser_urdf.show_collision
        )

    @show_meshes_cb.on_update
    def _(_):
        viser_urdf.show_visual = show_meshes_cb.value

    @show_collision_meshes_cb.on_update
    def _(_):
        viser_urdf.show_collision = show_collision_meshes_cb.value

    # Hide checkboxes if meshes are not loaded.
    show_meshes_cb.visible = load_meshes
    show_collision_meshes_cb.visible = load_collision_meshes

    # Set initial robot configuration.
    viser_urdf.update_cfg(np.array(initial_config))

    # Create grid, sized for this robot's footprint.
    trimesh_scene = viser_urdf._urdf.scene or viser_urdf._urdf.collision_scene
    server.scene.add_grid(
        "/grid",
        width=6,
        height=6,
        position=(
            0.0,
            0.0,
            # Get the minimum z value of the trimesh scene.
            trimesh_scene.bounds[0, 2] if trimesh_scene is not None else 0.0,
        ),
    )

    # Create joint reset button.
    reset_button = server.gui.add_button("Reset")

    @reset_button.on_click
    def _(_):
        for s, init_q in zip(slider_handles, initial_config):
            s.value = init_q

    # Sleep forever.
    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    tyro.cli(main)
