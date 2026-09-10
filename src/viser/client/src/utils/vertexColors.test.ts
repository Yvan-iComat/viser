import { describe, expect, test } from "vitest";
import * as THREE from "three";
import { srgbUint8ToLinearFloat32 } from "./vertexColors";

describe("srgbUint8ToLinearFloat32", () => {
  test("matches THREE.Color's sRGB -> linear conversion", () => {
    // The contract that makes `vertex_colors=(r, g, b)` render identically to
    // `color=(r, g, b)`: three runs the uniform color through this same
    // transfer function, but leaves vertex color attributes untouched.
    const bytes = new Uint8Array([0, 1, 10, 90, 128, 200, 254, 255, 64]);
    const linear = srgbUint8ToLinearFloat32(bytes, null);

    const reference = new THREE.Color();
    for (let i = 0; i < bytes.length; i++) {
      reference.setRGB(bytes[i] / 255, 0, 0, THREE.SRGBColorSpace);
      expect(linear[i]).toBeCloseTo(reference.r, 6);
    }
  });

  test("maps the endpoints exactly", () => {
    const linear = srgbUint8ToLinearFloat32(new Uint8Array([0, 255]), null);
    expect(linear[0]).toBe(0);
    expect(linear[1]).toBe(1);
  });

  test("reuses the destination buffer when the length matches", () => {
    const out = new Float32Array(3);
    const same = srgbUint8ToLinearFloat32(new Uint8Array([255, 0, 0]), out);
    // Same reference => syncBufferGeometry stays on its bufferSubData path.
    expect(same).toBe(out);

    const different = srgbUint8ToLinearFloat32(new Uint8Array([255, 0]), out);
    expect(different).not.toBe(out);
    expect(different.length).toBe(2);
  });
});
