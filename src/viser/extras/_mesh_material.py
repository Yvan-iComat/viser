"""Explicit PBR material overrides for trimesh meshes.

Meshes sent through :meth:`viser.SceneApi.add_mesh_trimesh` are exported as
glTF, so their look is driven by glTF metallic-roughness factors. Formats that
carry no material (STL, PLY, OBJ without .mtl) export without any, and three.js
then falls back to the glTF default -- white, ``metalness=1``, ``roughness=1``
-- which renders as dull bare metal. :class:`MeshMaterial` lets callers state
the material explicitly.
"""

from __future__ import annotations

import dataclasses
from typing import Tuple, Union

import numpy as np
import trimesh
from trimesh.visual import ColorVisuals, TextureVisuals
from trimesh.visual.material import PBRMaterial, SimpleMaterial

from .._assignable_props_api import colors_to_uint8

RgbColor = Union[Tuple[int, int, int], Tuple[float, float, float]]


@dataclasses.dataclass(frozen=True)
class MeshMaterial:
    """PBR material properties for a mesh. Fields left as None keep the value
    from the mesh's own material (or the defaults listed below when it has
    none, e.g. STL files).

    Colors are sRGB tuples, like other viser color arguments (and color
    pickers, RAL/hex codes): integers are read as [0, 255] and floats as
    [0, 1]. They are converted to the linear values glTF stores.
    """

    color: RgbColor | None = None
    """Base color. Default: the mesh's own color (URDF ``<color>`` for STL)."""
    roughness: float | None = None
    """0 = mirror-like, 1 = fully matte. Default 1.0."""
    metalness: float | None = None
    """0 = dielectric (paint, plastic, rubber), 1 = bare metal. Default 0.0.
    Metals take their color from reflections, so they need an environment map
    (:meth:`viser.SceneApi.configure_environment_map`) to look right."""
    opacity: float | None = None
    """1 = opaque. Default: the mesh's own alpha."""
    emissive: RgbColor | None = None
    """Self-illumination color, e.g. for status lights. Default black."""
    double_sided: bool | None = None
    """Render back faces too, for open (non-watertight) shells."""


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_linear_rgb(color: RgbColor) -> np.ndarray:
    rgb = colors_to_uint8(np.asarray(color))
    assert rgb.shape == (3,), f"Expected an RGB color, got {color!r}."
    return _srgb_to_linear(rgb.astype(np.float64) / 255.0)


def _srgba_uint8_to_linear(rgba: np.ndarray) -> np.ndarray:
    out = np.asarray(rgba, dtype=np.float64) / 255.0
    out[:3] = _srgb_to_linear(out[:3])
    return out


def _base_color_rgba(visual: object) -> np.ndarray | None:
    """Existing uniform base color of a mesh as linear float RGBA, or None
    if the mesh has per-vertex/per-face color variation that a single
    material factor cannot represent.

    Material factors from the file (glTF, COLLADA via trimesh) are already
    linear; plain colors (URDF ``<color>``, STL/PLY colors) are sRGB."""
    if isinstance(visual, TextureVisuals):
        material = visual.material
        if isinstance(material, PBRMaterial) and material.baseColorFactor is not None:
            return np.asarray(material.baseColorFactor, dtype=np.float64) / 255.0
        main = getattr(material, "main_color", None)
        if main is not None:
            return np.asarray(main, dtype=np.float64) / 255.0
        return np.ones(4)
    if isinstance(visual, ColorVisuals):
        if visual.kind is None:
            return _srgba_uint8_to_linear(visual.main_color)
        colors = visual.vertex_colors if visual.kind == "vertex" else visual.face_colors
        if len(colors) == 0 or np.all(colors == colors[0]):
            return _srgba_uint8_to_linear(visual.main_color)
        return None
    return np.ones(4)


def apply_mesh_material(
    mesh: trimesh.Trimesh, material: MeshMaterial | None = None
) -> trimesh.Trimesh:
    """Return a copy of `mesh` whose visual is a glTF PBR material.

    The existing material (or uniform color) is used as the base and the
    non-None fields of `material` are layered on top. Textures and UVs of
    textured meshes are kept. Meshes with varying vertex/face colors are
    returned unchanged unless `material.color` replaces them.

    With `material=None`, color-only meshes (e.g. from STL) still get a
    dielectric material instead of the glTF default bare-metal one.
    """
    material = material if material is not None else MeshMaterial()
    visual = mesh.visual
    base = _base_color_rgba(visual)
    if base is None and material.color is None:
        return mesh

    old = visual.material if isinstance(visual, TextureVisuals) else None
    if isinstance(old, PBRMaterial):
        old_pbr: PBRMaterial | None = old
    elif isinstance(old, SimpleMaterial):
        old_pbr = old.to_pbr()
    else:
        old_pbr = None

    rgba = np.ones(4) if base is None else base.copy()
    if material.color is not None:
        rgba[:3] = _to_linear_rgb(material.color)
    if material.opacity is not None:
        rgba[3] = float(np.clip(material.opacity, 0.0, 1.0))

    def pick(override: float | None, current: float | None, default: float) -> float:
        if override is not None:
            return float(override)
        return default if current is None else float(current)

    roughness = pick(
        material.roughness, None if old_pbr is None else old_pbr.roughnessFactor, 1.0
    )
    metalness = pick(
        material.metalness, None if old_pbr is None else old_pbr.metallicFactor, 0.0
    )
    if material.emissive is not None:
        emissive = _to_linear_rgb(material.emissive)
    elif old_pbr is not None and old_pbr.emissiveFactor is not None:
        emissive = np.asarray(old_pbr.emissiveFactor, dtype=np.float64)
    else:
        emissive = np.zeros(3)
    double_sided = (
        material.double_sided
        if material.double_sided is not None
        else bool(old_pbr is not None and old_pbr.doubleSided)
    )

    pbr = PBRMaterial(
        name=None if old_pbr is None else old_pbr.name,
        baseColorFactor=(rgba * 255.0).round().astype(np.uint8),
        baseColorTexture=None if old_pbr is None else old_pbr.baseColorTexture,
        normalTexture=None if old_pbr is None else old_pbr.normalTexture,
        occlusionTexture=None if old_pbr is None else old_pbr.occlusionTexture,
        emissiveTexture=None if old_pbr is None else old_pbr.emissiveTexture,
        metallicRoughnessTexture=(
            None if old_pbr is None else old_pbr.metallicRoughnessTexture
        ),
        roughnessFactor=float(np.clip(roughness, 0.0, 1.0)),
        metallicFactor=float(np.clip(metalness, 0.0, 1.0)),
        emissiveFactor=emissive,
        alphaMode="BLEND" if rgba[3] < 1.0 else "OPAQUE",
        doubleSided=double_sided,
    )
    uv = getattr(visual, "uv", None) if isinstance(visual, TextureVisuals) else None
    out = mesh.copy(include_cache=True)
    out.visual = TextureVisuals(uv=uv, material=pbr)
    return out
