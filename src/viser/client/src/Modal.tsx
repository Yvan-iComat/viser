import { ViewerContext } from "./ViewerContext";
import { GuiModalMessage } from "./WebsocketMessages";
import GeneratedGuiContainer from "./ControlPanel/Generated";
import { Modal } from "@mantine/core";
import { useContext } from "react";
import { shallowArrayEqual } from "./utils/shallowArrayEqual";

export function ViserModal() {
  const viewer = useContext(ViewerContext)!;

  const modalList = viewer.useGui((state) => state.modals, shallowArrayEqual);
  const modals = modalList.map((conf, index) => {
    return <GeneratedModal key={conf.uuid} conf={conf} index={index} />;
  });

  return modals;
}

function GeneratedModal({
  conf,
  index,
}: {
  conf: GuiModalMessage;
  index: number;
}) {
  // Convert size to proper Mantine format
  // Mantine accepts: "xs" | "sm" | "md" | "lg" | "xl" or number (pixels)
  let modalSize: string | number;

  // Check if it's a preset size string
  const presetSizes = ["xs", "sm", "md", "lg", "xl"];
  if (presetSizes.includes(conf.size)) {
    // Pass preset sizes directly as strings
    modalSize = conf.size;
  } else if (conf.size.endsWith("px")) {
    // Convert "800px" to number 800
    modalSize = parseInt(conf.size.replace("px", ""), 10);
  } else {
    // For other formats (percentages, etc.), pass as string
    modalSize = conf.size;
  }

  return (
    <Modal
      opened={true}
      title={conf.title}
      size={modalSize}
      onClose={() => {
        // To make memory management easier, we should only close modals from
        // the server.
        // Otherwise, the client would need to communicate to the server that
        // the modal was deleted and contained GUI elements were cleared.
      }}
      withCloseButton={false}
      centered
      zIndex={100 + index}
    >
      <GeneratedGuiContainer containerUuid={conf.uuid} />
    </Modal>
  );
}
