// Pins the framing contract for "fit all in" (toolbar reframe_view).
//
// The bug these guard: fitToSphere was handed the three.js scene ROOT, so the
// grid -- an ordinary Mesh with a 10x10 plane by default, and ~1010 units when
// infinite -- dominated the bounding sphere and framed the user's model down to
// a speck.

import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  EXCLUDE_FROM_BOUNDS,
  computeSceneBounds,
  computeSceneBoundingSphere,
} from "./sceneBounds";

/** A unit cube centered at the origin, i.e. bounds [-0.5, 0.5]^3. */
function unitCube(): THREE.Mesh {
  return new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));
}

/** Stand-in for the grid: a large plane, optionally marked as excluded. */
function gridLikePlane(size: number, excluded: boolean): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size, size));
  if (excluded) mesh.userData[EXCLUDE_FROM_BOUNDS] = true;
  return mesh;
}

describe("computeSceneBounds", () => {
  it("returns an empty box for a scene with no geometry", () => {
    const scene = new THREE.Scene();
    scene.add(new THREE.DirectionalLight(), new THREE.Group());
    expect(computeSceneBounds(scene).isEmpty()).toBe(true);
  });

  it("measures a mesh in world space, including ancestor transforms", () => {
    const scene = new THREE.Scene();
    const parent = new THREE.Group();
    parent.position.set(10, 0, 0);
    parent.scale.setScalar(2);
    parent.add(unitCube());
    scene.add(parent);

    const bounds = computeSceneBounds(scene);
    // Unit cube scaled 2x => extent 2, centered at x=10.
    expect(bounds.min.toArray()).toEqual([9, -1, -1]);
    expect(bounds.max.toArray()).toEqual([11, 1, 1]);
  });

  it("ignores objects marked EXCLUDE_FROM_BOUNDS", () => {
    const scene = new THREE.Scene();
    scene.add(unitCube());
    scene.add(gridLikePlane(10, true));

    const bounds = computeSceneBounds(scene);
    expect(bounds.min.toArray()).toEqual([-0.5, -0.5, -0.5]);
    expect(bounds.max.toArray()).toEqual([0.5, 0.5, 0.5]);
  });

  it("skips the entire subtree beneath an excluded ancestor", () => {
    const scene = new THREE.Scene();
    scene.add(unitCube());

    // Mirrors the label manager: an excluded group whose CHILDREN carry the
    // geometry. Marking only the parent must be enough.
    const labelGroup = new THREE.Group();
    labelGroup.userData[EXCLUDE_FROM_BOUNDS] = true;
    const faraway = unitCube();
    faraway.position.set(500, 500, 500);
    labelGroup.add(faraway);
    scene.add(labelGroup);

    const bounds = computeSceneBounds(scene);
    expect(bounds.max.toArray()).toEqual([0.5, 0.5, 0.5]);
  });

  it("skips invisible objects and their descendants", () => {
    const scene = new THREE.Scene();
    scene.add(unitCube());

    const hidden = new THREE.Group();
    hidden.visible = false;
    const child = unitCube();
    child.position.set(100, 0, 0);
    hidden.add(child);
    scene.add(hidden);

    expect(computeSceneBounds(scene).max.x).toBe(0.5);
  });

  it("includes points and lines, not just meshes", () => {
    const scene = new THREE.Scene();

    const pointsGeometry = new THREE.BufferGeometry();
    pointsGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array([0, 0, 0, 3, 4, 5]), 3),
    );
    scene.add(new THREE.Points(pointsGeometry));

    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array([0, 0, 0, -1, -2, -3]), 3),
    );
    scene.add(new THREE.Line(lineGeometry));

    const bounds = computeSceneBounds(scene);
    expect(bounds.min.toArray()).toEqual([-1, -2, -3]);
    expect(bounds.max.toArray()).toEqual([3, 4, 5]);
  });

  it("unions instance transforms for batched meshes", () => {
    const scene = new THREE.Scene();
    // THREE.InstancedMesh exposes computeBoundingBox()/boundingBox, the same
    // structural contract as the vendored InstancedMesh2, so it exercises the
    // batched path (geometry alone would understate the bounds).
    const instanced = new THREE.InstancedMesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshBasicMaterial(),
      2,
    );
    instanced.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 0, 0));
    instanced.setMatrixAt(1, new THREE.Matrix4().makeTranslation(20, 0, 0));
    scene.add(instanced);

    const bounds = computeSceneBounds(scene);
    expect(bounds.min.x).toBeCloseTo(-0.5);
    expect(bounds.max.x).toBeCloseTo(20.5);
  });

  it("is unaffected by a huge excluded grid (the reported bug)", () => {
    const scene = new THREE.Scene();
    const model = unitCube();
    scene.add(model);
    // An infinite grid's shader scales its plane by 1 + fadeDistance (100).
    scene.add(gridLikePlane(1010, true));

    const sphere = computeSceneBoundingSphere(scene)!;
    // Must reflect the cube (radius ~0.87), not the grid (radius ~700).
    expect(sphere.radius).toBeLessThan(1);
    expect(sphere.center.length()).toBeCloseTo(0);
  });
});

describe("repeated fitting is idempotent", () => {
  // The user-visible symptom of measuring a camera-relative object: each click
  // of "fit model in view" zoomed out a bit more, forever. Any object whose
  // world size depends on camera distance (drei's PivotControls with
  // fixed={true}, the camera-locked background plane, the pixel-sized
  // crosshair) closes a feedback loop: fit -> camera moves -> object grows ->
  // next fit is wider. Simulated here by rescaling with the "camera distance"
  // that the previous fit produced.
  function simulateFits(excluded: boolean): number[] {
    const scene = new THREE.Scene();
    scene.add(unitCube());

    const cameraScaled = new THREE.Group();
    if (excluded) cameraScaled.userData[EXCLUDE_FROM_BOUNDS] = true;
    cameraScaled.add(unitCube());
    scene.add(cameraScaled);

    const radii: number[] = [];
    let cameraDistance = 3;
    for (let i = 0; i < 6; i++) {
      // Constant apparent (pixel) size => world scale grows with distance.
      cameraScaled.scale.setScalar(0.5 * cameraDistance);
      const sphere = computeSceneBoundingSphere(scene)!;
      radii.push(sphere.radius);
      // Roughly what fitToSphere does for a 50-degree vertical FOV.
      cameraDistance = sphere.radius / Math.sin((50 * Math.PI) / 180 / 2);
    }
    return radii;
  }

  it("keeps the framing radius constant when the widget is excluded", () => {
    const radii = simulateFits(true);
    for (const radius of radii) expect(radius).toBeCloseTo(radii[0], 10);
  });

  it("demonstrates the runaway if such a widget is NOT excluded", () => {
    // Guards the exclusion's purpose: without it the radius grows every click.
    const radii = simulateFits(false);
    expect(radii[radii.length - 1]).toBeGreaterThan(radii[0]);
  });
});

describe("computeSceneBoundingSphere", () => {
  it("returns null when there is nothing to frame", () => {
    expect(computeSceneBoundingSphere(new THREE.Scene())).toBeNull();
  });

  it("gives a degenerate single point a small finite radius", () => {
    const scene = new THREE.Scene();
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array([7, 7, 7]), 3),
    );
    scene.add(new THREE.Points(geometry));

    const sphere = computeSceneBoundingSphere(scene)!;
    // radius 0 would make fitToSphere collapse the camera onto the target.
    expect(sphere.radius).toBeGreaterThan(0);
    expect(Number.isFinite(sphere.radius)).toBe(true);
    expect(sphere.center.toArray()).toEqual([7, 7, 7]);
  });

  it("picks up matrix changes made since the last render", () => {
    const scene = new THREE.Scene();
    const cube = unitCube();
    scene.add(cube);
    // Moved without calling updateMatrixWorld: a click can land before R3F's
    // next render, so computeSceneBounds must refresh matrices itself.
    cube.position.set(50, 0, 0);

    expect(computeSceneBoundingSphere(scene)!.center.x).toBeCloseTo(50);
  });
});
