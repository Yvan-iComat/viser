import { Progress } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck } from "@tabler/icons-react";
import React from "react";
import { v4 as uuid } from "uuid";
import { ViewerContextContents } from "../ViewerContext";

/** Show (and keep updated) a progress notification for the upload tracked
 * under `uploadKey` in `uploadsInProgress`. Returns the tracked state. */
export function useUploadProgressNotification(
  viewer: ViewerContextContents,
  uploadKey: string,
) {
  const uploadState = viewer.useGui(
    (state) => state.uploadsInProgress[uploadKey],
  );
  const totalBytes = uploadState?.totalBytes;

  // Cache total bytes string.
  const totalBytesString = React.useMemo(() => {
    if (totalBytes === undefined) return "";
    let displaySize = totalBytes;
    const displayUnits = ["B", "K", "M", "G", "T", "P"];
    let displayUnitIndex = 0;
    while (displaySize >= 100 && displayUnitIndex < displayUnits.length - 1) {
      displaySize /= 1024;
      displayUnitIndex += 1;
    }
    return `${displaySize.toFixed(1)}${displayUnits[displayUnitIndex]}`;
  }, [totalBytes]);

  // Track which notifications we've already shown, so the first effect run
  // creates the notification (notifications.show) and later runs update it
  // (notifications.update is a no-op if the id doesn't exist yet).
  const shownNotificationsRef = React.useRef<Set<string>>(new Set());

  // Update notification status.
  React.useEffect(() => {
    if (uploadState === undefined) return;
    const { notificationId, filename } = uploadState;

    // Guard against 0/0 = NaN for empty (zero-byte) files: they complete
    // immediately with uploadedBytes == totalBytes == 0, so treat them as done
    // rather than leaving the spinner up forever.
    const progressValue =
      uploadState.totalBytes === 0
        ? 1.0
        : uploadState.uploadedBytes / uploadState.totalBytes;
    const isDone = progressValue >= 1.0;

    const body = {
      id: notificationId,
      title: "Uploading " + `${filename} (${totalBytesString})`,
      message: isDone ? (
        "File uploaded successfully."
      ) : (
        <Progress
          size="sm"
          transitionDuration={10}
          value={100 * progressValue}
        />
      ),
      autoClose: isDone,
      withCloseButton: isDone,
      loading: !isDone,
      icon: isDone ? <IconCheck /> : undefined,
    };

    if (shownNotificationsRef.current.has(notificationId)) {
      notifications.update(body);
    } else {
      shownNotificationsRef.current.add(notificationId);
      notifications.show(body);
    }
  }, [uploadState, totalBytesString]);

  return uploadState;
}

/** Stream `file` to the server in chunks, on behalf of the component
 * `componentUuid`. Progress is tracked in `uploadsInProgress[uploadKey]`:
 * upload buttons pass their own uuid, drop zones pass the transfer uuid so
 * concurrent files don't overwrite each other's progress. */
export async function sendFileUpload(
  viewer: ViewerContextContents,
  componentUuid: string,
  uploadKey: string,
  file: File,
  transferUuid: string = uuid(),
) {
  // Get viewer mutable once.
  const viewerMutable = viewer.mutable.current;

  const chunkSize = 512 * 1024; // bytes
  const numChunks = Math.ceil(file.size / chunkSize);
  const notificationId = "upload-" + transferUuid;

  // Begin upload by setting initial state.
  viewer.guiActions.updateUploadState({
    componentId: uploadKey,
    uploadedBytes: 0,
    totalBytes: file.size,
    filename: file.name,
    notificationId,
  });

  viewerMutable.sendMessage({
    type: "FileTransferStartUpload",
    source_component_uuid: componentUuid,
    transfer_uuid: transferUuid,
    filename: file.name,
    mime_type: file.type,
    size_bytes: file.size,
    part_count: numChunks,
  });

  for (let i = 0; i < numChunks; i++) {
    const start = i * chunkSize;
    const end = (i + 1) * chunkSize;
    const chunk = file.slice(start, end);
    const buffer = await chunk.arrayBuffer();

    viewerMutable.sendMessage({
      type: "FileTransferPart",
      source_component_uuid: componentUuid,
      transfer_uuid: transferUuid,
      part_index: i,
      content: new Uint8Array(buffer),
    });
  }
}
