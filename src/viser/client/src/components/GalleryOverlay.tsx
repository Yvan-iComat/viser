import React from "react";
import { Box } from "@mantine/core";
import { ViewerContext } from "../ViewerContext";
import { GuiComponentContext } from "../ControlPanel/GuiComponentContext";
import GalleryComponent from "./Gallery";
import * as Messages from "../WebsocketMessages";

/** Renders one gallery, reading its live config from the component store.
 *
 * Reports its visibility upward so the overlay can unmount its opaque backdrop
 * when every gallery is hidden -- otherwise a `visible=False` gallery would
 * still cover the canvas.
 */
function GalleryOverlayItem({
  uuid,
  onVisibilityChange,
}: {
  uuid: string;
  onVisibilityChange: (uuid: string, visible: boolean) => void;
}) {
  const viewer = React.useContext(ViewerContext)!;
  const conf = viewer.useGuiConfig(uuid);
  // Inline galleries are rendered by Generated.tsx inside their container, so
  // the overlay must ignore them entirely -- including for its backdrop.
  const isWindow =
    conf?.type === "GuiGalleryMessage" && conf.props.placement === "window";
  const visible = isWindow && conf.props.visible;

  React.useEffect(() => {
    onVisibilityChange(uuid, visible);
    return () => onVisibilityChange(uuid, false);
  }, [uuid, visible, onVisibilityChange]);

  if (conf === undefined || conf.type !== "GuiGalleryMessage" || !isWindow) {
    return null;
  }
  return <GalleryComponent {...conf} />;
}

/** Full-window overlay hosting all galleries.
 *
 * Galleries deliberately live outside the dock layout: they cover the viewport
 * as their own surface, so keeping them independent of the dock avoids
 * entangling them with panel placement and layout ops. Rendered as a sibling of
 * the canvas, above it in paint order.
 *
 * The overlay only mounts when at least one visible gallery exists, so the
 * canvas keeps receiving pointer events in the common case.
 */
export function GalleryOverlay() {
  const viewer = React.useContext(ViewerContext)!;
  const galleryUuids = viewer.useGui(
    (state) => state.galleryUuids,
    (a, b) => a.length === b.length && a.every((u, i) => u === b[i]),
  );
  // Sent unthrottled: block clicks are discrete events, and the throttled
  // sender coalesces to the latest message -- two quick clicks on different
  // blocks would drop the first one.
  const messageSender = React.useCallback(
    (message: Messages.Message) => {
      viewer.mutable.current.sendMessage?.(message);
    },
    [viewer],
  );
  const [visibleUuids, setVisibleUuids] = React.useState<
    Record<string, boolean>
  >({});

  const onVisibilityChange = React.useCallback(
    (uuid: string, visible: boolean) =>
      setVisibleUuids((prev) =>
        prev[uuid] === visible ? prev : { ...prev, [uuid]: visible },
      ),
    [],
  );

  const guiContext = React.useMemo(
    () => ({
      folderDepth: 0,
      messageSender,
      // Galleries have no value state and host no children, so these two are
      // unused; they exist to satisfy the context shape.
      setValue: () => undefined,
      GuiContainer: () => null,
    }),
    [messageSender],
  );

  if (galleryUuids.length === 0) return null;

  // Only paint (and capture pointer events) when something is actually shown.
  const anyVisible = galleryUuids.some((uuid) => visibleUuids[uuid]);

  return (
    <GuiComponentContext.Provider value={guiContext}>
      <Box
        style={{
          position: "absolute",
          inset: 0,
          // Above the canvas but BELOW every dock layer (panes 5, floating
          // windows 10+, resizers 15, drop hints 1000), so docked panels stay
          // visible and draggable over the gallery.
          zIndex: 2,
          overflowY: anyVisible ? "auto" : "hidden",
          backgroundColor: anyVisible ? "#fff" : undefined,
          pointerEvents: anyVisible ? undefined : "none",
        }}
      >
        {galleryUuids.map((uuid) => (
          <GalleryOverlayItem
            key={uuid}
            uuid={uuid}
            onVisibilityChange={onVisibilityChange}
          />
        ))}
      </Box>
    </GuiComponentContext.Provider>
  );
}
