import { Box, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFileUpload } from "@tabler/icons-react";
import React from "react";
import { v4 as uuid } from "uuid";
import { ViewerContext, ViewerContextContents } from "../ViewerContext";
import { sendFileUpload, useUploadProgressNotification } from "./fileUpload";

/** Inset (px) of the dashed rectangle from the edges of the viewer area. */
const ZONE_INSET_PX = 12;

/** Fallback for clearing the drag highlight when the drag leaves the window
 * without a `dragleave` we can attribute to it (or the user cancels with Esc).
 * `dragover` fires at least every ~350ms while a drag is over the page. */
const DRAG_IDLE_TIMEOUT_MS = 1000;

type DragState = "none" | "outside" | "inside";

function fileMatchesExtensions(
  filename: string,
  extensions: readonly string[],
): boolean {
  if (extensions.length === 0) return true;
  const lower = filename.toLowerCase();
  return extensions.some((ext) => lower.endsWith(ext));
}

function isFileDrag(event: DragEvent): boolean {
  return event.dataTransfer?.types.includes("Files") ?? false;
}

/**
 * File drop zone covering the 3D viewer area (registered from Python with
 * `GuiApi.add_file_drop_zone`). A dashed rectangle delimits the zone; files
 * released inside it are filtered by extension and uploaded to the server,
 * which hands them to the zone's `on_drop` callbacks.
 *
 * The overlay never captures pointer events: drags are tracked with window
 * listeners and hit-tested against the rectangle, so the scene and panels stay
 * fully interactive.
 */
export function FileDropZoneOverlay() {
  const viewer = React.useContext(ViewerContext)!;
  const zones = viewer.useGui((state) => state.fileDropZones);
  // The most recently registered enabled zone receives drops.
  const activeZone = React.useMemo(() => {
    const enabled = Object.values(zones).filter((z) => !z.props.disabled);
    return enabled.length === 0 ? null : enabled[enabled.length - 1];
  }, [zones]);

  const rectRef = React.useRef<HTMLDivElement>(null);
  const [dragState, setDragState] = React.useState<DragState>("none");
  const [transferUuids, setTransferUuids] = React.useState<string[]>([]);

  // Read through a ref inside the window listeners, so prop updates (e.g. new
  // accepted extensions) don't require re-binding them.
  const activeZoneRef = React.useRef(activeZone);
  activeZoneRef.current = activeZone;

  const zoneActive = activeZone !== null;
  React.useEffect(() => {
    if (!zoneActive) {
      setDragState("none");
      return;
    }

    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const clearDrag = () => {
      clearTimeout(idleTimer);
      setDragState("none");
    };
    const isInsideZone = (event: DragEvent) => {
      const rect = rectRef.current?.getBoundingClientRect();
      if (rect === undefined) return false;
      return (
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom
      );
    };

    const onDragOver = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(clearDrag, DRAG_IDLE_TIMEOUT_MS);
      // Another drop target (e.g. the chat panel's attachments) already
      // claimed this drag: leave it alone.
      if (event.defaultPrevented) {
        setDragState("outside");
        return;
      }
      // Cancelling dragover is what allows a drop, and also stops the
      // browser from navigating away to the file when it's released.
      event.preventDefault();
      const inside = isInsideZone(event);
      if (event.dataTransfer !== null) {
        event.dataTransfer.dropEffect = inside ? "copy" : "none";
      }
      setDragState(inside ? "inside" : "outside");
    };
    const onDragLeave = (event: DragEvent) => {
      // `relatedTarget === null` means the drag left the document.
      if (isFileDrag(event) && event.relatedTarget === null) clearDrag();
    };
    const onDrop = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      clearDrag();
      if (event.defaultPrevented) return;
      event.preventDefault();
      const zone = activeZoneRef.current;
      if (zone === null || !isInsideZone(event)) return;
      const started = handleDroppedFiles(
        viewer,
        zone.uuid,
        zone.props.accepted_extensions,
        Array.from(event.dataTransfer?.files ?? []),
      );
      if (started.length > 0) {
        setTransferUuids((prev) => [...prev, ...started]);
      }
    };

    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      clearTimeout(idleTimer);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [zoneActive, viewer]);

  const onTransferDone = React.useCallback((transferUuid: string) => {
    setTransferUuids((prev) => prev.filter((t) => t !== transferUuid));
  }, []);

  const progress = transferUuids.map((transferUuid) => (
    <DropUploadProgress
      key={transferUuid}
      transferUuid={transferUuid}
      onDone={onTransferDone}
    />
  ));
  if (activeZone === null) return <>{progress}</>;

  const { text, accepted_extensions, show_border } = activeZone.props;
  const dragging = dragState !== "none";
  const hovered = dragState === "inside";
  // The rectangle stays mounted (just transparent) when hidden, so drops can
  // still be hit-tested against it.
  const shown = show_border || dragging;

  return (
    <>
      {progress}
      <Box
        ref={rectRef}
        style={{
          position: "absolute",
          inset: ZONE_INSET_PX,
          // At rest: above the canvas and galleries (2) but below dock panes
          // (5) and the toolbar (9). While dragging files, above everything
          // so the highlight is never hidden behind a panel.
          zIndex: dragging ? 1001 : 3,
          pointerEvents: "none",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: "0.75em",
          border: `2px dashed ${
            hovered
              ? "var(--mantine-primary-color-filled)"
              : "var(--mantine-color-dimmed)"
          }`,
          backgroundColor: hovered
            ? "color-mix(in srgb, var(--mantine-primary-color-filled) 10%, transparent)"
            : undefined,
          opacity: shown ? 1 : 0,
          transition: "opacity 120ms ease, background-color 120ms ease",
        }}
      >
        <Stack
          align="center"
          gap={4}
          px="md"
          py="sm"
          style={{
            borderRadius: "0.5em",
            backgroundColor:
              "color-mix(in srgb, var(--mantine-color-body) 75%, transparent)",
          }}
        >
          <IconFileUpload
            size={32}
            stroke={1.5}
            color={
              hovered
                ? "var(--mantine-primary-color-filled)"
                : "var(--mantine-color-dimmed)"
            }
          />
          <Text size="md" fw={500}>
            {text}
          </Text>
          {accepted_extensions.length > 0 && (
            <Text size="xs" c="dimmed">
              {accepted_extensions.join(", ")}
            </Text>
          )}
        </Stack>
      </Box>
    </>
  );
}

/** Filter dropped files by extension (notifying about rejected ones) and start
 * uploading the accepted ones. Returns the transfer uuids of started uploads. */
function handleDroppedFiles(
  viewer: ViewerContextContents,
  zoneUuid: string,
  acceptedExtensions: readonly string[],
  files: File[],
): string[] {
  const accepted = files.filter((f) =>
    fileMatchesExtensions(f.name, acceptedExtensions),
  );
  const rejected = files.filter((f) => !accepted.includes(f));
  if (rejected.length > 0) {
    notifications.show({
      title:
        rejected.length === 1
          ? `Unsupported file: ${rejected[0].name}`
          : `${rejected.length} unsupported files`,
      message: `Accepted file types: ${acceptedExtensions.join(", ")}`,
      color: "red",
      autoClose: 6000,
      withCloseButton: true,
    });
  }
  return accepted.map((file) => {
    // Progress is keyed by transfer so concurrent files don't collide.
    const transferUuid = uuid();
    void sendFileUpload(viewer, zoneUuid, transferUuid, file, transferUuid);
    return transferUuid;
  });
}

/** Shows the progress notification for one dropped file; unmounts itself
 * (via `onDone`) once the upload completes. */
function DropUploadProgress({
  transferUuid,
  onDone,
}: {
  transferUuid: string;
  onDone: (transferUuid: string) => void;
}) {
  const viewer = React.useContext(ViewerContext)!;
  const uploadState = useUploadProgressNotification(viewer, transferUuid);
  const done =
    uploadState !== undefined &&
    uploadState.uploadedBytes >= uploadState.totalBytes;
  React.useEffect(() => {
    if (done) onDone(transferUuid);
  }, [done, onDone, transferUuid]);
  return null;
}
