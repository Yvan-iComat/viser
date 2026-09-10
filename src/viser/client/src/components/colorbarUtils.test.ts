import { describe, expect, test } from "vitest";
import { gradientCss, placeTicks } from "./colorbarUtils";

describe("gradientCss", () => {
  test("spaces the ramp's control points evenly across the bar", () => {
    const colors = new Uint8Array([0, 0, 0, 128, 128, 128, 255, 255, 255]);
    expect(gradientCss(colors, "top")).toBe(
      "linear-gradient(to top, rgb(0,0,0) 0.0000%, " +
        "rgb(128,128,128) 50.0000%, rgb(255,255,255) 100.0000%)",
    );
  });

  test("honors the direction, so vmax lands at the right end", () => {
    const colors = new Uint8Array([1, 2, 3, 4, 5, 6]);
    expect(gradientCss(colors, "right")).toContain("linear-gradient(to right,");
  });
});

describe("placeTicks", () => {
  test("converts data values to 0-1 positions along the bar", () => {
    expect(
      placeTicks(
        [
          [0, "0"],
          [50, "50"],
          [100, "100"],
        ],
        0,
        100,
      ),
    ).toEqual([
      { fraction: 0, text: "0" },
      { fraction: 0.5, text: "50" },
      { fraction: 1, text: "100" },
    ]);
  });

  test("handles a negative vmin", () => {
    expect(placeTicks([[0, "zero"]], -10, 10)).toEqual([
      { fraction: 0.5, text: "zero" },
    ]);
  });

  test("drops ticks outside the range", () => {
    expect(
      placeTicks(
        [
          [-1, "low"],
          [0.5, "mid"],
          [2, "high"],
        ],
        0,
        1,
      ),
    ).toEqual([{ fraction: 0.5, text: "mid" }]);
  });

  test("keeps endpoint ticks despite float error", () => {
    // (0.3 - 0) / 0.3 can land a hair above 1; the top tick must survive and
    // be clamped rather than dropped.
    const placed = placeTicks([[0.1 + 0.2, "top"]], 0, 0.3);
    expect(placed).toHaveLength(1);
    expect(placed[0].fraction).toBe(1);
  });

  test("drops everything when the range is degenerate", () => {
    // No position to occupy: every value maps to the same point.
    expect(placeTicks([[5, "five"]], 5, 5)).toEqual([]);
  });
});
