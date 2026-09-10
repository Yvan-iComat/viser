import { Box, Text } from "@mantine/core";
import { GuiColorbarMessage } from "../WebsocketMessages";
import { gradientCss, placeTicks } from "./colorbarUtils";

/** Half a line of text, so ticks at the very ends don't collide with
 * neighboring content. */
const END_PADDING = "0.45em";

function TickLabel({ text }: { text: string }) {
  return (
    <Text
      fz="xs"
      style={{
        lineHeight: 1,
        // Keeps digits from shifting as values change, e.g. while animating.
        fontVariantNumeric: "tabular-nums",
        whiteSpace: "nowrap",
      }}
    >
      {text}
    </Text>
  );
}

function ColorbarComponent(message: GuiColorbarMessage) {
  if (!message.props.visible) return null;
  return <ColorbarComponentInner {...message} />;
}

function ColorbarComponentInner({ props }: GuiColorbarMessage) {
  const vertical = props.orientation === "vertical";
  const ticks = placeTicks(props.ticks, props.vmin, props.vmax);
  // `to top` / `to right` put vmax at the top / right, matching how the ticks
  // are laid out below.
  const gradient = gradientCss(props.colors, vertical ? "top" : "right");

  const bar = (
    <Box
      style={{
        width: vertical ? props.thickness : props.length,
        height: vertical ? props.length : props.thickness,
        borderRadius: 3,
        background: gradient,
        // An inset ring instead of a border: a border would sit outside the
        // gradient box and shift the ticks out of alignment with it.
        boxShadow: "inset 0 0 0 1px var(--mantine-color-default-border)",
        flexShrink: 0,
      }}
    />
  );

  return (
    <Box px="xs" pb="0.5em">
      {props.label === null ? null : (
        <Text fz="sm" style={{ display: "block", marginBottom: "0.4em" }}>
          {props.label}
        </Text>
      )}
      {vertical ? (
        <Box
          style={{
            display: "flex",
            alignItems: "flex-start",
            // Room for the half-line overflow of the end ticks.
            padding: `${END_PADDING} 0`,
          }}
        >
          {bar}
          <Box
            style={{ position: "relative", height: props.length, flexGrow: 1 }}
          >
            {ticks.map(({ fraction, text }, index) => (
              <Box
                key={index}
                style={{
                  position: "absolute",
                  top: `${(1 - fraction) * 100}%`,
                  left: 0,
                  transform: "translateY(-50%)",
                  display: "flex",
                  alignItems: "center",
                  gap: "5px",
                }}
              >
                <Box
                  style={{
                    width: 6,
                    height: 1,
                    background: "currentColor",
                    opacity: 0.55,
                  }}
                />
                <TickLabel text={text} />
              </Box>
            ))}
          </Box>
        </Box>
      ) : (
        <Box style={{ display: "inline-block", padding: `0 ${END_PADDING}` }}>
          {bar}
          <Box
            style={{
              position: "relative",
              width: props.length,
              height: "1.5em",
            }}
          >
            {ticks.map(({ fraction, text }, index) => (
              <Box
                key={index}
                style={{
                  position: "absolute",
                  left: `${fraction * 100}%`,
                  top: 0,
                  transform: "translateX(-50%)",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: "3px",
                }}
              >
                <Box
                  style={{
                    width: 1,
                    height: 4,
                    background: "currentColor",
                    opacity: 0.55,
                  }}
                />
                <TickLabel text={text} />
              </Box>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
}

export default ColorbarComponent;
