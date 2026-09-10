/**
 * Per-vertex color attribute helpers.
 *
 * viser's uint8 color convention is sRGB everywhere in the API: a uniform
 * `color=(90, 200, 255)` reaches the material through `THREE.Color.setHex`,
 * which three converts sRGB -> linear working space before shading. Vertex
 * color ATTRIBUTES get no such conversion -- three (like glTF's COLOR_0)
 * assumes they are already linear. Uploading raw sRGB bytes would therefore
 * render dark and over-saturated, and a constant `vertex_colors` array would
 * NOT match the same value passed as `color`.
 *
 * So we convert on upload. The transfer function has only 256 possible inputs,
 * so a lookup table reduces it to one indexed read per channel.
 */
const SRGB_TO_LINEAR_LUT = /* @__PURE__ */ (() => {
  const lut = new Float32Array(256);
  for (let i = 0; i < 256; i++) {
    const c = i / 255;
    lut[i] =
      c < 0.04045 ? c * (1.0 / 12.92) : Math.pow((c + 0.055) / 1.055, 2.4);
  }
  return lut;
})();

/**
 * Convert sRGB uint8 RGB triplets to linear float32.
 *
 * Writes into `out` when it is non-null and already the right length. Reusing
 * the destination keeps `syncBufferGeometry` on its allocation-free fast path
 * for streaming recolors: same constructor + same length means the existing GL
 * buffer is re-uploaded via bufferSubData rather than reallocated.
 */
export function srgbUint8ToLinearFloat32(
  src: Uint8Array,
  out: Float32Array | null,
): Float32Array {
  const dst =
    out !== null && out.length === src.length
      ? out
      : new Float32Array(src.length);
  for (let i = 0; i < src.length; i++) {
    dst[i] = SRGB_TO_LINEAR_LUT[src[i]];
  }
  return dst;
}
