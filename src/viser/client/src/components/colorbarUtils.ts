/**
 * Pure geometry/color helpers for the colorbar component. Split out from
 * `Colorbar.tsx` so they can be unit tested without rendering.
 */

/**
 * Build a CSS gradient from the ramp's control points, spaced evenly.
 *
 * CSS interpolates between gradient stops linearly in sRGB, which is exactly
 * what a colormap lookup does over the same stops -- so the bar reproduces the
 * mapping rather than approximating it, as long as the caller sends the ramp
 * densely enough for its own curvature.
 */
export function gradientCss(colors: Uint8Array, toward: string): string {
  const count = Math.floor(colors.length / 3);
  const stops: string[] = [];
  for (let i = 0; i < count; i++) {
    const percent = count === 1 ? 0 : (i / (count - 1)) * 100;
    stops.push(
      `rgb(${colors[i * 3]},${colors[i * 3 + 1]},${colors[i * 3 + 2]}) ` +
        `${percent.toFixed(4)}%`,
    );
  }
  return `linear-gradient(to ${toward}, ${stops.join(", ")})`;
}

/** Ticks converted from data values to 0-1 positions along the bar. Ticks
 * outside the range (or any tick at all, when the range is degenerate) have no
 * position to occupy and are dropped. */
export function placeTicks(
  ticks: [number, string][],
  vmin: number,
  vmax: number,
): { fraction: number; text: string }[] {
  const span = vmax - vmin;
  if (span === 0) return [];
  const placed = [];
  for (const [value, text] of ticks) {
    const fraction = (value - vmin) / span;
    // Tolerate float error at the endpoints rather than dropping the 0% and
    // 100% ticks, which are the two that matter most.
    if (fraction < -1e-9 || fraction > 1 + 1e-9) continue;
    placed.push({ fraction: Math.min(1, Math.max(0, fraction)), text });
  }
  return placed;
}
