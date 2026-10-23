import { GuiUploadButtonMessage } from "../WebsocketMessages";
import { Box } from "@mantine/core";

import { Button } from "@mantine/core";
import React, { useContext } from "react";
import { ViewerContext, ViewerContextContents } from "../ViewerContext";
import { sendFileUpload, useUploadProgressNotification } from "./fileUpload";
import { htmlIconWrapper } from "./ComponentStyles.css";
import { toMantineColor } from "./colorUtils";

export default function UploadButtonComponent({
  uuid,
  props: { disabled, mime_type, color, _icon_html: icon_html, label },
}: GuiUploadButtonMessage) {
  // Handle GUI input types.
  const viewer = useContext(ViewerContext)!;
  const fileUploadRef = React.useRef<HTMLInputElement>(null);
  const { isUploading, upload } = useFileUpload({
    viewer,
    componentUuid: uuid,
  });

  return (
    <Box mx="xs" mb="0.5em">
      <input
        type="file"
        style={{ display: "none" }}
        id={`file_upload_${uuid}`}
        name="file"
        accept={mime_type}
        ref={fileUploadRef}
        onChange={(e) => {
          const input = e.target as HTMLInputElement;
          if (!input.files) return;
          upload(input.files[0]);
        }}
      />
      <Button
        id={uuid}
        fullWidth
        color={toMantineColor(color)}
        onClick={() => {
          if (fileUploadRef.current === null) return;
          fileUploadRef.current.value = fileUploadRef.current.defaultValue;
          fileUploadRef.current.click();
        }}
        style={{ height: "2em" }}
        disabled={disabled || isUploading}
        size="sm"
        leftSection={
          icon_html === null ? undefined : (
            <div
              className={htmlIconWrapper}
              dangerouslySetInnerHTML={{ __html: icon_html }}
            />
          )
        }
      >
        {label}
      </Button>
    </Box>
  );
}

function useFileUpload({
  viewer,
  componentUuid,
}: {
  componentUuid: string;
  viewer: ViewerContextContents;
}) {
  const uploadState = useUploadProgressNotification(viewer, componentUuid);

  const isUploading =
    uploadState !== undefined &&
    uploadState.uploadedBytes < uploadState.totalBytes;

  async function upload(file: File) {
    await sendFileUpload(viewer, componentUuid, componentUuid, file);
  }

  return {
    isUploading,
    upload,
  };
}
