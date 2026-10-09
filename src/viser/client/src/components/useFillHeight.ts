import React, { useEffect, useState } from "react";

/** Height that makes a component fill its container's scroll viewport.
 *
 * Panel bodies (and the control panel) wrap content in a Mantine ScrollArea,
 * so CSS `height: 100%` has nothing definite to resolve against. Instead,
 * measure: the viewport height minus everything else in the scrolled content
 * (padding, sibling components) is the space left for the component.
 *
 * In a content-sized container (an un-resized floating panel) the viewport
 * equals the content, so this is a fixed point at the current height and the
 * `initial` height is kept. In a fixed-height one (docked or resized panel)
 * the component grows or shrinks with it, down to `minHeight`.
 *
 * Also returns a negative top margin that absorbs the container's top padding
 * when the component is its first child, so it sits flush under the tab strip. */
export function useFillHeight(
  ref: React.RefObject<HTMLDivElement | null>,
  initial: number,
  enabled: boolean,
  minHeight: number,
) {
  const [height, setHeight] = useState(initial);
  const [marginTop, setMarginTop] = useState(0);
  useEffect(() => {
    const root = ref.current;
    const viewport = root?.closest<HTMLElement>(".mantine-ScrollArea-viewport");
    const content = viewport?.firstElementChild;
    if (!enabled || !root || !viewport || !(content instanceof HTMLElement))
      return;
    const parent = root.parentElement;
    if (parent !== null && parent.firstElementChild === root) {
      setMarginTop(-(parseFloat(getComputedStyle(parent).paddingTop) || 0));
    }
    const update = () => {
      // Fractional rects, floored once: integer offset/client heights round
      // independently and can overshoot the viewport by a pixel.
      const other =
        content.getBoundingClientRect().height -
        root.getBoundingClientRect().height;
      const target = Math.max(
        minHeight,
        Math.floor(viewport.getBoundingClientRect().height - other),
      );
      // Tolerance avoids a resize-observer ping-pong on sub-pixel rounding.
      setHeight((h) => (Math.abs(h - target) < 1 ? h : target));
    };
    const observer = new ResizeObserver(update);
    observer.observe(viewport);
    observer.observe(content);
    update();
    return () => observer.disconnect();
  }, [ref, enabled, minHeight]);
  return { height, marginTop };
}
