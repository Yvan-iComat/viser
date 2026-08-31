// Bounding-volume computation for "fit all in" (the toolbar's reframe_view
// action).
//
// Framing must be driven by the user's actual geometry, NOT by the whole
// three.js scene root. Passing the root to camera-controls' fitToSphere pulls
// in reference/helper objects, and the grid in particular dwarfs typical
// content: the default grid is 10x10 world units, and an infinite grid's vertex
// shader scales its plane by `1 + fadeDistance` (default 100), i.e. ~1010 units
// -- so a 1-unit model would be framed ~1000x too far out. The infinite grid
// also has followCamera, so its world bounds MOVE with the camera and framing
// off it isn't even idempotent.
//
// Excluded objects opt out at their source by setting
// `userData[EXCLUDE_FROM_BOUNDS] = true` on the object (or on any ancestor --
// the whole subtree is then skipped). A marker beats type-sniffing here because
// the grid is an ordinary THREE.Mesh with real plane geometry and is otherwise
// indistinguishable from user content.

import * as THREE from "three";

/**
 * Marker key: set `object.userData[EXCLUDE_FROM_BOUNDS] = true` to omit an
 * object and its entire subtree from "fit all in" framing.
 */
export const EXCLUDE_FROM_BOUNDS = "viserExcludeFromBounds";

/** Scratch object -- computeSceneBounds can run per click, so avoid garbage. */
const scratchBox = new THREE.Box3();

/**
 * An InstancedMesh2 (vendored instanced-mesh) subclasses THREE.Mesh, so its
 * geometry describes only ONE instance; the per-instance transforms live in a
 * texture. Its own computeBoundingBox() unions the active instances, which is
 * what we actually want. Detected structurally rather than via an import, to
 * avoid a hard dependency (and to also catch drei's InstancedMesh-alikes).
 */
interface BatchedBoundsProvider extends THREE.Object3D {
  boundingBox: THREE.Box3 | null;
  computeBoundingBox: () => void;
}

function asBatched(object: THREE.Object3D): BatchedBoundsProvider | null {
  const candidate = object as Partial<BatchedBoundsProvider>;
  return typeof candidate.computeBoundingBox === "function" &&
    "boundingBox" in candidate
    ? (object as BatchedBoundsProvider)
    : null;
}

/**
 * True for objects whose geometry should contribute to framing.
 *
 * THREE.Sprite and troika's text objects are deliberately absent: they're sized
 * in screen space, so their world bounds are meaningless for framing (a label
 * far from the model would drag the camera out with no visible geometry there).
 */
function isGeometryCarrier(object: THREE.Object3D): boolean {
  const o = object as THREE.Mesh | THREE.Points | THREE.Line;
  // Points/Line/LineSegments/LineLoop and Mesh all expose `geometry`. Checking
  // the flags rather than `instanceof` keeps this working across duplicate
  // three copies in the bundle (a real hazard with vendored/split deps).
  return (
    ((o as THREE.Mesh).isMesh === true ||
      (o as THREE.Points).isPoints === true ||
      (o as THREE.Line).isLine === true) &&
    (o as THREE.Mesh).geometry !== undefined
  );
}

/**
 * Union of the world-space bounds of every visible geometry-carrying object
 * under `root`, skipping subtrees marked with {@link EXCLUDE_FROM_BOUNDS}.
 *
 * @returns an empty Box3 when the scene holds no qualifying geometry.
 */
export function computeSceneBounds(root: THREE.Object3D): THREE.Box3 {
  const bounds = new THREE.Box3().makeEmpty();

  // Bounds are read in world space, so the matrices must be current. A click
  // can land before R3F's next render, and objects added this tick (or moved by
  // a useFrame that runs after the last update) would otherwise use stale
  // matrixWorlds.
  root.updateMatrixWorld(true);

  // Explicit stack rather than Object3D.traverse: traverse() has no way to
  // prune, and we must not descend into excluded subtrees.
  const stack: THREE.Object3D[] = [root];

  while (stack.length > 0) {
    const object = stack.pop()!;

    // `visible` is inherited in three's renderer, so skipping here correctly
    // drops the whole hidden subtree. Note the root itself is checked too: a
    // hidden root means nothing is drawn, hence nothing to frame.
    if (!object.visible || object.userData[EXCLUDE_FROM_BOUNDS] === true) {
      continue;
    }

    const batched = asBatched(object);
    if (batched !== null) {
      // Recomputed every call: instance transforms and active/visible counts
      // change freely, and a cached boundingBox goes stale silently.
      batched.computeBoundingBox();
      if (batched.boundingBox !== null && !batched.boundingBox.isEmpty()) {
        scratchBox.copy(batched.boundingBox).applyMatrix4(object.matrixWorld);
        bounds.union(scratchBox);
      }
    } else if (isGeometryCarrier(object)) {
      const geometry = (object as THREE.Mesh).geometry;
      if (geometry.boundingBox === null) geometry.computeBoundingBox();
      const geometryBox = geometry.boundingBox;
      // Degenerate/NaN geometry (e.g. an empty position attribute) yields a
      // non-finite box that would poison the union and make framing collapse.
      if (geometryBox !== null && isFiniteBox(geometryBox)) {
        scratchBox.copy(geometryBox).applyMatrix4(object.matrixWorld);
        bounds.union(scratchBox);
      }
    }

    for (const child of object.children) stack.push(child);
  }

  return bounds;
}

/** Guards against NaN/Infinity, which propagate through Box3.union silently. */
function isFiniteBox(box: THREE.Box3): boolean {
  return (
    Number.isFinite(box.min.x) &&
    Number.isFinite(box.min.y) &&
    Number.isFinite(box.min.z) &&
    Number.isFinite(box.max.x) &&
    Number.isFinite(box.max.y) &&
    Number.isFinite(box.max.z)
  );
}

/**
 * Bounding sphere over the scene's real geometry, ready for fitToSphere.
 *
 * @returns null when there's nothing to frame, so callers can leave the camera
 * alone rather than diving toward a degenerate target.
 */
export function computeSceneBoundingSphere(
  root: THREE.Object3D,
): THREE.Sphere | null {
  const bounds = computeSceneBounds(root);
  if (bounds.isEmpty()) return null;

  const sphere = bounds.getBoundingSphere(new THREE.Sphere());

  // A single point/flat plane gives radius 0, which makes fitToSphere divide
  // down to a degenerate distance (the camera ends up inside the target).
  // Fall back to a small but finite radius so the object stays on screen.
  if (!(sphere.radius > 0)) sphere.radius = 1e-3;

  return sphere;
}
