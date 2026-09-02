import * as THREE from "three";
import { STLLoader } from "three-stdlib";

/** Render an STL mesh to a PNG snapshot, offscreen.
 *
 * Uses a throwaway WebGL context rather than the app's R3F canvas: this is a
 * one-shot render with no React tree, and borrowing the live canvas would mean
 * mutating the user's scene and camera to take a picture of something else.
 *
 * The renderer, geometry, and materials are all disposed before returning, so
 * repeated calls don't leak GL contexts.
 *
 * Returns PNG bytes, or null if the mesh could not be parsed or rendered.
 */
export async function renderStlSnapshot(
  stlData: Uint8Array,
  width: number,
  height: number,
): Promise<Uint8Array<ArrayBuffer> | null> {
  let renderer: THREE.WebGLRenderer | null = null;
  let geometry: THREE.BufferGeometry | null = null;
  const disposables: { dispose: () => void }[] = [];

  try {
    // STLLoader.parse wants an ArrayBuffer; make sure we hand it exactly the
    // bytes of this view (a Uint8Array may be a window onto a larger buffer).
    const buffer = stlData.buffer.slice(
      stlData.byteOffset,
      stlData.byteOffset + stlData.byteLength,
    ) as ArrayBuffer;
    geometry = new STLLoader().parse(buffer);
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    const bounds = geometry.boundingBox;
    const sphere = geometry.boundingSphere;
    if (bounds === null || sphere === null || sphere.radius === 0) return null;

    const scene = new THREE.Scene();

    // Center the mesh on the origin, sitting on top of the grid plane.
    const center = bounds.getCenter(new THREE.Vector3());
    const material = new THREE.MeshStandardMaterial({
      color: 0xa8adb5,
      metalness: 0.1,
      roughness: 0.65,
      flatShading: false,
    });
    disposables.push(material);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(-center.x, -center.y, -bounds.min.z);
    scene.add(mesh);

    // Grid on the ground plane, echoing the main scene's look.
    const grid = new THREE.GridHelper(
      sphere.radius * 4,
      10,
      0xc9ced6,
      0xe4e8ee,
    );
    // GridHelper is XZ-planar; viser meshes are Z-up, so lay it flat in XY.
    grid.rotation.x = Math.PI / 2;
    scene.add(grid);
    disposables.push(grid.material as THREE.Material, grid.geometry);

    scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.0);
    keyLight.position.set(1, -1.4, 2).multiplyScalar(sphere.radius * 3);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0xffffff, 0.6);
    fillLight.position.set(-1.5, 1, 0.5).multiplyScalar(sphere.radius * 3);
    scene.add(fillLight);

    // Fixed 3/4 view, framed from the bounding sphere so any mesh scale fits.
    const fov = 30;
    const camera = new THREE.PerspectiveCamera(fov, width / height, 0.01, 1e6);
    camera.up.set(0, 0, 1);
    const distance =
      (sphere.radius / Math.sin((fov * Math.PI) / 360)) * 1.15 + sphere.radius;
    const dir = new THREE.Vector3(1, -1, 0.65).normalize();
    // Aim at the mesh's mid-height, since it was placed sitting on z=0.
    const midHeight = (bounds.max.z - bounds.min.z) / 2;
    camera.position.copy(dir.multiplyScalar(distance));
    camera.position.z += midHeight;
    camera.lookAt(0, 0, midHeight);

    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      // Required to read pixels back after render on some drivers.
      preserveDrawingBuffer: true,
    });
    renderer.setSize(width, height, false);
    renderer.setPixelRatio(1);
    renderer.setClearColor(0xf8f9fa, 1);
    renderer.render(scene, camera);

    const blob = await new Promise<Blob | null>((resolve) =>
      renderer!.domElement.toBlob(resolve, "image/png"),
    );
    if (blob === null) return null;
    return new Uint8Array(await blob.arrayBuffer());
  } catch (e) {
    console.error("Gallery STL snapshot failed:", e);
    return null;
  } finally {
    geometry?.dispose();
    for (const d of disposables) d.dispose();
    // forceContextLoss frees the GL context immediately instead of waiting for
    // GC; browsers cap the number of live contexts.
    renderer?.dispose();
    renderer?.forceContextLoss();
  }
}
