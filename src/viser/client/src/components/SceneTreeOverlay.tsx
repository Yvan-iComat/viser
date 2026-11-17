import React from "react";
import { Box } from "@mantine/core";
import { ViewerContext } from "../ViewerContext";
import SceneTreePanelComponent from "./SceneTreePanel";

/** Renders one "window" scene tree from its live config. Inline trees are
 * rendered by Generated.tsx inside their container instead. */
function SceneTreeOverlayItem({ uuid }: { uuid: string }) {
  const viewer = React.useContext(ViewerContext)!;
  const conf = viewer.useGuiConfig(uuid);
  if (
    conf === undefined ||
    conf.type !== "GuiSceneTreeMessage" ||
    conf.props.placement !== "window"
  ) {
    return null;
  }
  return <SceneTreePanelComponent {...conf} />;
}

/** Layer hosting floating scene tree panels over the viewer area.
 *
 * Like galleries, these live outside the dock layout: a scene tree window is
 * a lightweight overlay with a see-through background, not a dockable panel.
 * The layer itself never takes pointer events -- only the panels do -- so the
 * canvas stays interactive around them. */
export function SceneTreeOverlay() {
  const viewer = React.useContext(ViewerContext)!;
  const sceneTreeUuids = viewer.useGui(
    (state) => state.sceneTreeUuids,
    (a, b) => a.length === b.length && a.every((u, i) => u === b[i]),
  );
  if (sceneTreeUuids.length === 0) return null;
  return (
    <Box
      style={{
        position: "absolute",
        inset: 0,
        // Above the canvas and galleries (2), below every dock layer (panes 5,
        // floating windows 10+, resizers 15, drop hints 1000).
        zIndex: 3,
        pointerEvents: "none",
        overflow: "hidden",
      }}
    >
      {sceneTreeUuids.map((uuid) => (
        <SceneTreeOverlayItem key={uuid} uuid={uuid} />
      ))}
    </Box>
  );
}
