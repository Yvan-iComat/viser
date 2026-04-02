import { GuiFolderSelectButtonMessage } from "../WebsocketMessages";
import { Box } from "@mantine/core";

import { Button } from "@mantine/core";
import React, { useContext } from "react";
import { ViewerContext } from "../ViewerContext";
import { htmlIconWrapper } from "./ComponentStyles.css";
import { toMantineColor } from "./colorUtils";

export default function FolderSelectButtonComponent({
  uuid,
  props: { disabled, color, _icon_html: icon_html, label },
}: GuiFolderSelectButtonMessage) {
  // Handle GUI input types.
  const viewer = useContext(ViewerContext)!;
  const folderSelectRef = React.useRef<HTMLInputElement>(null);

  // Check if File System Access API is available (modern browsers)
  const supportsFileSystemAccess = 'showDirectoryPicker' in window;

  const handleModernFolderSelect = async () => {
    try {
      // Use File System Access API for absolute path support
      // @ts-ignore - showDirectoryPicker is not in TypeScript definitions yet
      const dirHandle = await window.showDirectoryPicker({
        mode: 'read',
      });

      // Get the full path if available
      // Note: resolve() is experimental and may not be available in all browsers
      let fullPath = dirHandle.name;

      // Try to get the full path using the experimental API
      if ('resolve' in dirHandle) {
        try {
          // @ts-ignore
          const pathSegments = await dirHandle.resolve(dirHandle);
          if (pathSegments) {
            fullPath = '/' + pathSegments.join('/');
          }
        } catch (err) {
          console.warn('Could not resolve full path:', err);
        }
      }

      // Send the folder path to the server
      viewer.mutable.current.sendMessage({
        type: "FolderSelectMessage",
        source_component_uuid: uuid,
        folder_path: fullPath,
      });

    } catch (err: any) {
      // User cancelled or error occurred
      if (err.name !== 'AbortError') {
        console.error('Error selecting folder:', err);
      }
    }
  };

  const handleLegacyFolderSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const input = e.target as HTMLInputElement;
    if (!input.files || input.files.length === 0) return;

    // Fallback: Extract folder name from webkitRelativePath
    // This only provides the folder name, not the absolute path
    const file = input.files[0];
    const relativePath = (file as any).webkitRelativePath;
    if (!relativePath) return;

    const folderName = relativePath.split('/')[0];

    viewer.mutable.current.sendMessage({
      type: "FolderSelectMessage",
      source_component_uuid: uuid,
      folder_path: folderName,
    });
  };

  const handleClick = () => {
    if (supportsFileSystemAccess) {
      // Use modern File System Access API
      handleModernFolderSelect();
    } else {
      // Fall back to legacy file input method
      if (folderSelectRef.current === null) return;
      folderSelectRef.current.click();
    }
  };

  return (
    <Box mx="xs" mb="0.5em">
      {/* Legacy fallback: only used in browsers without File System Access API */}
      <input
        type="file"
        style={{ display: "none" }}
        id={`folder_select_${uuid}`}
        name="folder"
        {...({ webkitdirectory: "" } as any)}
        ref={folderSelectRef}
        onChange={handleLegacyFolderSelect}
      />
      <Button
        id={uuid}
        fullWidth
        color={toMantineColor(color)}
        onClick={handleClick}
        style={{ height: "2em" }}
        disabled={disabled}
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
