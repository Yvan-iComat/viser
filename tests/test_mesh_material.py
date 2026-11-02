"""Tests for explicit PBR materials (`viser.extras.MeshMaterial`)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from trimesh.visual import ColorVisuals, TextureVisuals
from trimesh.visual.material import PBRMaterial

import viser
from viser.extras import MeshMaterial, ViserUrdf, apply_mesh_material

URDF_STL = """<?xml version="1.0"?>
<robot name="two_links">
  <material name="anodized"><color rgba="0.2 0.2 0.25 1"/></material>
  <link name="base">
    <visual>
      <geometry><mesh filename="base.stl"/></geometry>
      <material name="anodized"/>
    </visual>
  </link>
  <link name="arm">
    <visual><geometry><mesh filename="arm.stl"/></geometry></visual>
  </link>
  <joint name="j" type="revolute">
    <parent link="base"/><child link="arm"/>
    <origin xyz="0 0 1"/><axis xyz="0 0 1"/>
    <limit lower="-1" upper="1" effort="1" velocity="1"/>
  </joint>
</robot>
"""


def _glb_pbr(glb_data: bytes) -> dict:
    length = int.from_bytes(glb_data[12:16], "little")
    gltf = json.loads(glb_data[20 : 20 + length])
    (material,) = gltf["materials"]
    return material


def _base_color(mesh: trimesh.Trimesh) -> np.ndarray:
    assert isinstance(mesh.visual, TextureVisuals)
    material = mesh.visual.material
    assert isinstance(material, PBRMaterial)
    assert material.baseColorFactor is not None
    return np.asarray(material.baseColorFactor)


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def test_stl_without_material_gets_dielectric_default() -> None:
    mesh = trimesh.creation.box()
    assert isinstance(mesh.visual, ColorVisuals) and mesh.visual.kind is None
    out = apply_mesh_material(mesh)
    assert isinstance(out.visual, TextureVisuals)
    material = out.visual.material
    assert isinstance(material, PBRMaterial)
    assert material.metallicFactor == 0.0
    assert material.roughnessFactor == 1.0


def test_srgb_color_is_stored_linear() -> None:
    out = apply_mesh_material(
        trimesh.creation.box(),
        MeshMaterial(color=(255, 102, 0), roughness=0.3, metalness=0.5),
    )
    assert isinstance(out.visual, TextureVisuals)
    material = out.visual.material
    assert isinstance(material, PBRMaterial)
    base = np.asarray(material.baseColorFactor) / 255.0
    assert base[:3] == pytest.approx(
        [1.0, _srgb_to_linear(102 / 255), 0.0], abs=1 / 255
    )
    assert material.roughnessFactor == pytest.approx(0.3)
    assert material.metallicFactor == pytest.approx(0.5)
    # Integer and float tuples mean the same color.
    floats = apply_mesh_material(
        trimesh.creation.box(), MeshMaterial(color=(1.0, 0.4, 0.0))
    )
    assert np.allclose(_base_color(floats), _base_color(out), atol=1)


def test_unset_fields_keep_file_material() -> None:
    mesh = trimesh.creation.box()
    mesh.visual = TextureVisuals(
        material=PBRMaterial(
            baseColorFactor=[10, 20, 30, 255], roughnessFactor=0.8, metallicFactor=0.1
        )
    )
    out = apply_mesh_material(mesh, MeshMaterial(roughness=0.2))
    assert isinstance(out.visual, TextureVisuals)
    material = out.visual.material
    assert isinstance(material, PBRMaterial)
    assert list(_base_color(out)) == [10, 20, 30, 255]
    assert material.roughnessFactor == pytest.approx(0.2)
    assert material.metallicFactor == pytest.approx(0.1)
    # The input mesh is left alone.
    assert isinstance(mesh.visual, TextureVisuals)
    assert isinstance(mesh.visual.material, PBRMaterial)
    assert mesh.visual.material.roughnessFactor == pytest.approx(0.8)


def test_opacity_switches_to_blend() -> None:
    out = apply_mesh_material(trimesh.creation.box(), MeshMaterial(opacity=0.5))
    assert isinstance(out.visual, TextureVisuals)
    material = out.visual.material
    assert isinstance(material, PBRMaterial)
    assert material.alphaMode == "BLEND"
    assert _base_color(out)[3] == 128


def test_varying_vertex_colors_are_kept_without_color_override() -> None:
    mesh = trimesh.creation.box()
    assert isinstance(mesh.visual, ColorVisuals)
    mesh.visual.vertex_colors = np.tile(
        np.arange(8, dtype=np.uint8)[:, None] * 30, (1, 4)
    )
    assert apply_mesh_material(mesh, MeshMaterial(roughness=0.1)) is mesh


@pytest.fixture
def stl_urdf(tmp_path: Path) -> Path:
    trimesh.creation.box().export(tmp_path / "base.stl")
    trimesh.creation.cylinder(radius=0.2, height=1.0).export(tmp_path / "arm.stl")
    path = tmp_path / "robot.urdf"
    path.write_text(URDF_STL)
    return path


def _materials_by_link(urdf: ViserUrdf) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for handle in urdf._meshes:
        assert isinstance(handle, viser.GlbHandle)
        # Node names omit the root link: "/visual/base.stl", "/visual/arm/arm.stl".
        link = "arm" if "/arm/" in handle.name else "base"
        out[link] = _glb_pbr(handle.glb_data)
    return out


def test_urdf_materials_layer_by_key(stl_urdf: Path) -> None:
    server = viser.ViserServer(port=0)
    try:
        urdf = ViserUrdf(
            server,
            stl_urdf,
            show_feature_edges=False,
            materials={
                "*": MeshMaterial(roughness=0.4),
                "anodized": MeshMaterial(metalness=0.9),
                "arm": MeshMaterial(color=(255, 102, 0)),
            },
        )
        mats = _materials_by_link(urdf)
        base = mats["base"]["pbrMetallicRoughness"]
        arm = mats["arm"]["pbrMetallicRoughness"]
        # "*" applies everywhere; URDF material name and link name layer on top.
        assert base["roughnessFactor"] == pytest.approx(0.4)
        assert base["metallicFactor"] == pytest.approx(0.9)
        # URDF <color> (sRGB) survives, converted to linear.
        assert base["baseColorFactor"][0] == pytest.approx(
            _srgb_to_linear(0.2), abs=1 / 255
        )
        assert arm["roughnessFactor"] == pytest.approx(0.4)
        assert arm["metallicFactor"] == pytest.approx(0.0)
        assert arm["baseColorFactor"][:3] == pytest.approx(
            [1.0, _srgb_to_linear(102 / 255), 0.0], abs=1 / 255
        )
    finally:
        server.stop()


def test_urdf_material_rule_callable(stl_urdf: Path) -> None:
    server = viser.ViserServer(port=0)
    try:
        seen: list[tuple[str, str]] = []

        def rule(link: str, geometry: str, mesh: trimesh.Trimesh):
            seen.append((link, geometry))
            return MeshMaterial(roughness=0.25) if link == "arm" else None

        urdf = ViserUrdf(server, stl_urdf, show_feature_edges=False, materials=rule)
        mats = _materials_by_link(urdf)
        assert sorted(link for link, _ in seen) == ["arm", "base"]
        assert mats["arm"]["pbrMetallicRoughness"]["roughnessFactor"] == 0.25
        # Unmatched meshes still get a dielectric, not glTF's metal default.
        assert mats["base"]["pbrMetallicRoughness"]["metallicFactor"] == 0.0
    finally:
        server.stop()


def test_urdf_material_export_roundtrip(stl_urdf: Path) -> None:
    server = viser.ViserServer(port=0)
    try:
        urdf = ViserUrdf(
            server,
            stl_urdf,
            show_feature_edges=False,
            smooth_shading=True,
            materials={"*": MeshMaterial(roughness=0.5)},
        )
        for handle in urdf._meshes:
            assert isinstance(handle, viser.GlbHandle)
            loaded = trimesh.load(
                io.BytesIO(handle.glb_data), file_type="glb", force="mesh"
            )
            assert isinstance(loaded, trimesh.Trimesh)
            assert len(loaded.faces) > 0
    finally:
        server.stop()
