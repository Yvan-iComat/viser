/** Control panel width presets, keyed by the server's `control_width` theme
 * setting. Converted to px for the dock layout (em x the panel's 16px font
 * size). Custom CSS-style widths ("30em", "400px") are also accepted and
 * converted the same way. */
const CONTROL_WIDTH_EM: Record<string, number> = {
  small: 16,
  medium: 20,
  large: 24,
};

export function controlWidthPx(name: string): number {
  const preset = CONTROL_WIDTH_EM[name];
  if (preset !== undefined) return preset * 16;
  const match = /^(\d+(?:\.\d+)?)(em|px)?$/.exec(name.trim());
  if (match !== null) {
    const value = parseFloat(match[1]);
    return match[2] === "px" ? value : value * 16;
  }
  return 20 * 16;
}
