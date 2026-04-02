import { ActionIcon, Group, Paper, Tooltip, useMantineColorScheme } from "@mantine/core";
import {
  IconMaximize,
  IconCamera,
} from "@tabler/icons-react";
import * as TablerIcons from "@tabler/icons-react";
import { useContext } from "react";
import { ViewerContext } from "./ViewerContext";
import { ToolbarButton } from "./WebsocketMessages";

/**
 * Horizontal toolbar component docked at the top-middle of the main canvas.
 * Buttons can be fully customized via the buttons array, or defaults are shown if empty.
 */
export function HorizontalToolbar() {
  const viewer = useContext(ViewerContext)!;
  const { colorScheme } = useMantineColorScheme();

  // Get toolbar configuration from viewer state
  const toolbarConfig = viewer.useSceneTree((state) => state.toolbarConfig);

  // Don't render if toolbar is hidden
  if (toolbarConfig && !toolbarConfig.visible) {
    return null;
  }

  // Default buttons (when no buttons are specified)
  const defaultButtons: ToolbarButton[] = [
    {
      action: "reframe_view",
      icon: "IconMaximize",
      tooltip: "Fit model in view",
    },
    {
      action: "snapshot",
      icon: "IconCamera",
      tooltip: "Snapshot",
    },
  ];

  // Use provided buttons, or defaults if none specified
  const buttons = (toolbarConfig?.buttons && toolbarConfig.buttons.length > 0)
    ? toolbarConfig.buttons
    : defaultButtons;

  const handleAction = (action: string) => {
    // Special handling for reframe_view action
    if (action === "reframe_view") {
      const cameraControls = viewer.mutable.current.cameraControl;
      const scene = viewer.mutable.current.scene;
      if (cameraControls && scene) {
        cameraControls.fitToSphere(scene, true);
      }
    }

    // Send message to server for all actions
    viewer.mutable.current.sendMessage({
      type: "ToolbarActionMessage",
      action: action as any,
    });
  };

  // Helper to get Tabler icon component by name
  const getIconComponent = (iconName: string) => {
    const IconComponent = (TablerIcons as any)[iconName];
    return IconComponent || TablerIcons.IconQuestionMark;
  };

  const isDark = colorScheme === "dark";

  return (
    <Paper
      shadow="md"
      style={{
        position: "absolute",
        top: "4em", // Below titlebar (3.2em height)
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 9,
        backgroundColor: isDark
          ? "rgba(40, 40, 40, 0.9)"
          : "rgba(255, 255, 255, 0.9)",
        backdropFilter: "blur(10px)",
        borderRadius: "0.5em",
        padding: "0.5em",
        border: isDark
          ? "1px solid rgba(255, 255, 255, 0.15)"
          : "1px solid rgba(0, 0, 0, 0.15)",
      }}
    >
      <Group gap="xs">
        {/* Render all buttons in order */}
        {buttons.map((button: ToolbarButton, index: number) => {
          const IconComponent = getIconComponent(button.icon);
          return (
            <Tooltip key={index} label={button.tooltip} position="bottom">
              <ActionIcon
                variant="subtle"
                size="lg"
                onClick={() => handleAction(button.action)}
                aria-label={button.tooltip}
              >
                <IconComponent size="1.2em" />
              </ActionIcon>
            </Tooltip>
          );
        })}
      </Group>
    </Paper>
  );
}
