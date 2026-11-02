from __future__ import annotations

import dataclasses
import warnings
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Mapping, Tuple, Union

import numpy as np
import trimesh
from trimesh.scene import Scene
from trimesh.visual import ColorVisuals
from typing_extensions import assert_never

import viser

from .. import transforms as tf
from .._mesh_normals import split_vertices_at_creases
from ._mesh_material import MeshMaterial, apply_mesh_material

if TYPE_CHECKING:
    import yourdfpy

MaterialRule = Callable[[str, str, trimesh.Trimesh], Union[MeshMaterial, None]]
"""Called as ``rule(link_name, geometry_name, mesh)``; returns the material
for that visual mesh, or None to keep the mesh's own."""


class ViserUrdf:
    """Helper for rendering URDFs in Viser. This is a self-contained example
    that uses only basic Viser features. It can be copied and modified if you
    need more fine-grained control.

    To move or control visibility of the entire robot, you can create a
    parent frame that the URDF will be attached to. This is because
    ViserUrdf creates the robot's geometry as children of the specified
    `root_node_name`, but doesn't create the root node itself.

    .. code-block:: python

        import time
        import numpy as np
        import viser
        from viser.extras import ViserUrdf
        from robot_descriptions.loaders.yourdfpy import load_robot_description

        server = viser.ViserServer()

        # Create a parent frame for the robot.
        # ViserUrdf will attach the robot's geometry as children of this frame.
        robot_base = server.scene.add_frame("/robot", show_axes=False)

        # Load a URDF from robot_descriptions package.
        urdf = ViserUrdf(
            server,
            load_robot_description("panda_description"),
            root_node_name="/robot"
        )

        # Move the entire robot by updating the base frame.
        robot_base.position = (1.0, 0.0, 0.5)  # Move to (x=1, y=0, z=0.5).

        # Update joint configuration.
        urdf.update_cfg(np.array([0.0, 0.5, -0.5, 0.0, 0.0, 0.0, 0.0, 0.0]))

        # Make the robot blink.
        while True:
            robot_base.visible = False
            time.sleep(0.2)
            robot_base.visible = True
            time.sleep(3.0)

    Args:
        target: ViserServer or ClientHandle object to add URDF to.
        urdf_or_path: Either a path to a URDF file or a yourdfpy URDF object.
        scale: Scale factor to apply to resize the URDF.
        root_node_name: Viser scene tree name for the root of the URDF geometry.
        mesh_color_override: Optional color to override the URDF's visual mesh colors. RGB or RGBA tuple.
        collision_mesh_color_override: Optional color to override the URDF's collision mesh colors. RGB or RGBA tuple.
        load_meshes: If true, shows the URDF's visual meshes. If a yourdfpy
            URDF object is used as input, this expects the URDF to have a visual
            scene graph configured.
        load_collision_meshes: If true, shows the URDF's collision meshes. If a
            yourdfpy URDF object is used as input, this expects the URDF to have a
            collision scene graph configured.
        smooth_shading: Shade meshes with smooth vertex normals instead of one
            normal per face.
        crease_angle: With smooth_shading, edges whose faces meet at more than
            this angle (radians) stay sharp. None smooths across all edges.
            Also the threshold for `show_feature_edges`.
        sharp_edges: Optional extra sharp edges, keyed by geometry name in the
            URDF's trimesh scene (e.g. ``"link_1.dae_2"``) or by link name.
            Each value is an (E, 2) array of vertex index pairs into that
            geometry as loaded.
        show_feature_edges: Overlay the sharp edges as black line segments.
        materials: Explicit PBR materials for the visual meshes, needed for
            realistic rendering of formats without material data (STL). Either
            a mapping or a :data:`MaterialRule` callable. Mapping keys are
            matched against ``"*"`` (every mesh), the URDF ``<material>`` name,
            the link name and the geometry name; all matching entries are
            layered in that order, so more specific keys override individual
            fields of broader ones. Ignored when `mesh_color_override` is set.
    """

    def __init__(
        self,
        target: viser.ViserServer | viser.ClientHandle,
        urdf_or_path: yourdfpy.URDF | Path | str,
        scale: float = 1.0,
        root_node_name: str = "/",
        mesh_color_override: tuple[float, float, float]
        | tuple[float, float, float, float]
        | None = None,
        collision_mesh_color_override: tuple[float, float, float]
        | tuple[float, float, float, float]
        | None = None,
        load_meshes: bool = True,
        load_collision_meshes: bool = False,
        smooth_shading: bool = False,
        crease_angle: float | None = np.deg2rad(30.0),
        sharp_edges: Mapping[str, np.ndarray] | None = None,
        show_feature_edges: bool = True,
        materials: Mapping[str, MeshMaterial] | MaterialRule | None = None,
    ) -> None:
        assert root_node_name.startswith("/")
        assert len(root_node_name) == 1 or not root_node_name.endswith("/")

        try:
            import yourdfpy
        except ImportError as e:
            raise ImportError(
                "yourdfpy is required for ViserUrdf but is not installed. "
                "Install it with `pip install yourdfpy` or `pip install viser[urdf]`."
            ) from e

        if isinstance(urdf_or_path, (Path, str)):
            # Convert string path to Path object for consistent handling
            path_obj = (
                Path(urdf_or_path) if isinstance(urdf_or_path, str) else urdf_or_path
            )
            urdf = yourdfpy.URDF.load(
                path_obj,
                build_scene_graph=load_meshes,
                build_collision_scene_graph=load_collision_meshes,
                load_meshes=load_meshes,
                load_collision_meshes=load_collision_meshes,
                filename_handler=partial(
                    yourdfpy.filename_handler_magic,
                    dir=path_obj.parent,
                ),
            )
        else:
            urdf = urdf_or_path
        assert isinstance(urdf, yourdfpy.URDF)

        self._target = target
        self._urdf = urdf
        self._scale = scale
        self._root_node_name = root_node_name
        self._load_meshes = load_meshes
        self._smooth_shading = smooth_shading
        self._crease_angle = crease_angle
        self._sharp_edges = dict(sharp_edges) if sharp_edges is not None else {}
        self._show_feature_edges = show_feature_edges
        self._materials = materials
        self._collision_root_frame: viser.FrameHandle | None = None
        self._visual_root_frame: viser.FrameHandle | None = None
        self._joint_frames: List[viser.SceneNodeHandle] = []
        self._meshes: List[viser.SceneNodeHandle] = []
        num_joints_to_repeat = 0
        if load_meshes:
            if urdf.scene is not None:
                num_joints_to_repeat += 1
                self._visual_root_frame = self._add_joint_frames_and_meshes(
                    urdf.scene,
                    root_node_name,
                    collision_geometry=False,
                    mesh_color_override=mesh_color_override,
                )
            else:
                warnings.warn(
                    "load_meshes is enabled but the URDF model does not have a visual scene configured. Not displaying."
                )
        if load_collision_meshes:
            if urdf.collision_scene is not None:
                num_joints_to_repeat += 1
                self._collision_root_frame = self._add_joint_frames_and_meshes(
                    urdf.collision_scene,
                    root_node_name,
                    collision_geometry=True,
                    mesh_color_override=collision_mesh_color_override,
                )
            else:
                warnings.warn(
                    "load_collision_meshes is enabled but the URDF model does not have a collision scene configured. Not displaying."
                )

        self._joint_map_values = [*self._urdf.joint_map.values()] * num_joints_to_repeat

    @property
    def show_visual(self) -> bool:
        """Returns whether the visual meshes are currently visible."""
        return self._visual_root_frame is not None and self._visual_root_frame.visible

    @show_visual.setter
    def show_visual(self, visible: bool) -> None:
        """Set whether the visual meshes are currently visible."""
        if self._visual_root_frame is not None:
            self._visual_root_frame.visible = visible
        else:
            warnings.warn(
                "Cannot set `.show_visual`, since no visual meshes were loaded."
            )

    @property
    def show_collision(self) -> bool:
        """Returns whether the collision meshes are currently visible."""
        return (
            self._collision_root_frame is not None
            and self._collision_root_frame.visible
        )

    @show_collision.setter
    def show_collision(self, visible: bool) -> None:
        """Set whether the collision meshes are currently visible."""
        if self._collision_root_frame is not None:
            self._collision_root_frame.visible = visible
        else:
            warnings.warn(
                "Cannot set `.show_collision`, since no collision meshes were loaded."
            )

    def remove(self) -> None:
        """Remove URDF from scene."""
        # Some of this will be redundant, since children are removed when
        # parents are removed.
        for frame in self._joint_frames:
            frame.remove()
        for mesh in self._meshes:
            mesh.remove()

    def update_cfg(self, configuration: np.ndarray | dict[str, float]) -> None:
        """Update the joint angles of the visualized URDF.

        Args:
            configuration: Joint positions, either as an array ordered like
                :attr:`get_actuated_joint_names`, or as a mapping from joint
                name to position.
        """
        import yourdfpy

        self._urdf.update_cfg(configuration)
        for joint, frame_handle in zip(self._joint_map_values, self._joint_frames):
            assert isinstance(joint, yourdfpy.Joint)
            T_parent_child = self._urdf.get_transform(
                joint.child, joint.parent, collision_geometry=not self._load_meshes
            )
            frame_handle.wxyz = tf.SO3.from_matrix(T_parent_child[:3, :3]).wxyz
            frame_handle.position = T_parent_child[:3, 3] * self._scale

    def get_actuated_joint_limits(
        self,
    ) -> dict[str, tuple[float | None, float | None]]:
        """Returns an ordered mapping from actuated joint names to position limits."""
        import yourdfpy

        out: dict[str, tuple[float | None, float | None]] = {}
        for joint_name, joint in zip(
            self._urdf.actuated_joint_names, self._urdf.actuated_joints
        ):
            assert isinstance(joint_name, str)
            assert isinstance(joint, yourdfpy.Joint)
            if joint.limit is None:
                out[joint_name] = (-np.pi, np.pi)
            else:
                out[joint_name] = (joint.limit.lower, joint.limit.upper)
        return out

    def get_actuated_joint_names(self) -> Tuple[str, ...]:
        """Returns a tuple of actuated joint names, in order."""
        return tuple(self._urdf.actuated_joint_names)

    def _add_joint_frames_and_meshes(
        self,
        scene: Scene,
        root_node_name: str,
        collision_geometry: bool,
        mesh_color_override: tuple[float, float, float]
        | tuple[float, float, float, float]
        | None,
    ) -> viser.FrameHandle:
        """
        Helper function to add joint frames and meshes to the ViserUrdf object.
        """
        import yourdfpy

        prefix = "collision" if collision_geometry else "visual"
        prefixed_root_node_name = (f"{root_node_name}/{prefix}").replace("//", "/")
        root_frame = self._target.scene.add_frame(
            prefixed_root_node_name, show_axes=False
        )

        # Add coordinate frame for each joint.
        for joint in self._urdf.joint_map.values():
            assert isinstance(joint, yourdfpy.Joint)

            # # Extract position from origin matrix
            # if isinstance(joint.origin, np.ndarray) and joint.origin.shape == (4, 4):
            #     position = tuple(joint.origin[:3, 3])
            # else:
            #     position = (
            #         joint.origin[0][3],
            #         joint.origin[1][3],
            #         joint.origin[2][3],
            #     )

            # # Build hierarchical frame name
            # frame_name = _viser_name_from_frame(
            #     scene,
            #     joint.child,
            #     prefixed_root_node_name,
            # )

            # self._joint_frames.append(
            #     self._target.scene.add_frame(
            #         frame_name,
            #         show_axes=True,
            #         position=position
            #     )
            # )

            self._joint_frames.append(
                self._target.scene.add_frame(
                    _viser_name_from_frame(
                        scene,
                        joint.child,
                        prefixed_root_node_name,
                    ),
                    show_axes=False,
                )
            )

        # list of 10 colors
        colors = [
            (0.9, 0.1, 0.1),
            (0.1, 0.9, 0.1),
            (0.1, 0.1, 0.9),
            (0.9, 0.9, 0.1),
            (0.9, 0.1, 0.9),
            (0.1, 0.9, 0.9),
            (0.5, 0.5, 0.5),
            (0.9, 0.5, 0.1),
            (0.5, 0.1, 0.9),
            (0.1, 0.5, 0.9),
        ]

        # Add the URDF's meshes/geometry to viser.
        index_mesh = 0
        smooth_shading = self._smooth_shading
        crease_angle = self._crease_angle

        for link_name, mesh in scene.geometry.items():
            assert isinstance(mesh, trimesh.Trimesh)
            T_parent_child = self._urdf.get_transform(
                link_name,
                scene.graph.transforms.parents[link_name],
                collision_geometry=collision_geometry,
            )
            name = _viser_name_from_frame(scene, link_name, prefixed_root_node_name)

            # Scale + transform the mesh. (these will mutate it!)
            #
            # It's important that we use apply_transform() instead of unpacking
            # the rotation/translation terms, since the scene graph transform
            # can also contain scale and reflection terms.
            mesh = mesh.copy()
            mesh.apply_scale(self._scale)
            mesh.apply_transform(T_parent_child)

            # Geometry nodes hang off their link's frame.
            parent_link = scene.graph.transforms.parents[link_name]

            # User-listed sharp edges index the mesh as loaded, so the crease
            # split (which welds coincident vertices itself) must see it
            # unmerged.
            link_sharp_edges = self._sharp_edges.get(
                link_name, self._sharp_edges.get(parent_link)
            )
            if not smooth_shading or (
                crease_angle is None and link_sharp_edges is None
            ):
                # Merge duplicate vertices before exporting for smooth shading
                mesh.merge_vertices()

            # apply a color from a list (cycle through colors if more than 10 meshes)
            # Only to meshes with no color at all, so URDF <color> tags survive.
            visual = mesh.visual
            if isinstance(visual, ColorVisuals) and visual.kind is None:
                visual.vertex_colors = colors[index_mesh % len(colors)]
            index_mesh += 1

            if mesh_color_override is None:
                # Always set a glTF material: without one (STL), three.js falls
                # back to a white, fully metallic default.
                mesh = apply_mesh_material(
                    mesh,
                    None
                    if collision_geometry
                    else self._resolve_material(parent_link, link_name, mesh),
                )

            if mesh_color_override is None:
                self._meshes.append(
                    self._target.scene.add_mesh_trimesh(
                        name,
                        mesh,
                        smooth_shading=smooth_shading,
                        crease_angle=crease_angle,
                        sharp_edges=link_sharp_edges,
                    )
                )
            elif len(mesh_color_override) == 3:
                self._meshes.append(
                    self._target.scene.add_mesh_simple(
                        name,
                        mesh.vertices,
                        mesh.faces,
                        color=mesh_color_override,
                        smooth_shading=smooth_shading,
                        crease_angle=crease_angle,
                        sharp_edges=link_sharp_edges,
                    )
                )
            elif len(mesh_color_override) == 4:
                self._meshes.append(
                    self._target.scene.add_mesh_simple(
                        name,
                        mesh.vertices,
                        mesh.faces,
                        color=mesh_color_override[:3],
                        opacity=mesh_color_override[3],
                        smooth_shading=smooth_shading,
                        crease_angle=crease_angle,
                        sharp_edges=link_sharp_edges,
                    )
                )
            else:
                assert_never(mesh_color_override)

            # Yvan test debug: extract feature edges, and create a polyline mesh to overlay
            if not self._show_feature_edges:
                continue
            feature_edges, edge_coords = extract_feature_edges(
                mesh,
                np.degrees(crease_angle) if crease_angle is not None else 30.0,
                link_sharp_edges,
            )
            if len(edge_coords) == 0:
                continue

            # Use edge_coords for line segments, with a default color and line width
            self._target.scene.add_line_segments(
                name + "/feature_edges",
                points=edge_coords,
                colors=np.array([0.0, 0.0, 0.0]),  # black lines
                thickness=1.0,
                thickness_units="screen",
            )

        return root_frame

    def _urdf_material_name(self, link_name: str, geometry_name: str) -> str | None:
        """Name of the URDF <material> of the visual a geometry came from."""
        link = self._urdf.link_map.get(link_name)
        if link is None:
            return None
        visuals = [v for v in link.visuals if v.name == geometry_name]
        if not visuals and len(link.visuals) == 1:
            visuals = list(link.visuals)
        if len(visuals) != 1 or visuals[0].material is None:
            return None
        return visuals[0].material.name

    def _resolve_material(
        self, link_name: str, geometry_name: str, mesh: trimesh.Trimesh
    ) -> MeshMaterial | None:
        materials = self._materials
        if materials is None:
            return None
        if callable(materials):
            return materials(link_name, geometry_name, mesh)
        keys = ("*", self._urdf_material_name(link_name, geometry_name), link_name)
        resolved: MeshMaterial | None = None
        for key in (*keys, geometry_name):
            if key is None or key not in materials:
                continue
            layer = materials[key]
            if resolved is None:
                resolved = layer
            else:
                resolved = MeshMaterial(
                    **{
                        f.name: getattr(layer, f.name)
                        if getattr(layer, f.name) is not None
                        else getattr(resolved, f.name)
                        for f in dataclasses.fields(MeshMaterial)
                    }
                )
        return resolved


def _viser_name_from_frame(
    scene: Scene,
    frame_name: str,
    root_node_name: str = "/",
) -> str:
    """Given the (unique) name of a frame in our URDF's kinematic tree, return a
    scene node name for viser.

    For a robot manipulator with four frames, that looks like:


            ((shoulder)) == ((elbow))
               / /             |X|
              / /           ((wrist))
         ____/ /____           |X|
        [           ]       [=======]
        [ base_link ]        []   []
        [___________]


    this would map a name like "elbow" to "base_link/shoulder/elbow".
    """
    assert root_node_name.startswith("/")
    assert len(root_node_name) == 1 or not root_node_name.endswith("/")

    frames = []
    while frame_name != scene.graph.base_frame:
        frames.append(frame_name)
        frame_name = scene.graph.transforms.parents[frame_name]
    if root_node_name != "/":
        frames.append(root_node_name)
    return "/".join(frames[::-1])


def extract_feature_edges(
    mesh: trimesh.Trimesh,
    angle_tolerance_deg: float = 30.0,
    sharp_edges: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Extract feature edges: interior edges whose faces meet at more than
    `angle_tolerance_deg`, plus `sharp_edges` and non-manifold edges.

    Returns (E, 2) vertex index pairs and their (E, 2, 3) coordinates.
    """
    if len(mesh.faces) == 0:
        return np.zeros((0, 2), dtype=np.int64), np.zeros((0, 2, 3))
    feature_edges = split_vertices_at_creases(
        mesh.vertices, mesh.faces, np.radians(angle_tolerance_deg), sharp_edges
    ).feature_edges
    return feature_edges, np.asarray(mesh.vertices)[feature_edges]
