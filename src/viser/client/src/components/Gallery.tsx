import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, Text } from "@mantine/core";
import { GuiGalleryMessage } from "../WebsocketMessages";
import { GuiComponentContext } from "../ControlPanel/GuiComponentContext";
import { centeredIconWrapper } from "./ComponentStyles.css";

type GalleryBlock = GuiGalleryMessage["props"]["blocks"][number];

/** Build a blob URL for each block's snapshot, revoking them on change.
 *
 * Keyed by block_id so adding or retitling a block doesn't churn the URLs of
 * its siblings; only blocks whose image bytes actually changed are rebuilt. */
function useBlockImageUrls(blocks: readonly GalleryBlock[]) {
  const [urls, setUrls] = useState<Record<string, string>>({});

  // Depend on the image payloads only. Rebuilding on the `blocks` array
  // identity would churn every URL on any title/subtitle edit; a ref hands the
  // effect the current blocks without making them a dependency.
  const blocksRef = useRef(blocks);
  blocksRef.current = blocks;
  const dataKey = blocks
    .map((b) => `${b.block_id}:${b._data?.byteLength ?? -1}:${b._format}`)
    .join("|");

  useEffect(() => {
    const next: Record<string, string> = {};
    for (const block of blocksRef.current) {
      if (block._data === null) continue;
      next[block.block_id] = URL.createObjectURL(
        new Blob([block._data], { type: "image/" + block._format }),
      );
    }
    setUrls(next);
    return () => {
      for (const url of Object.values(next)) URL.revokeObjectURL(url);
    };
  }, [dataKey]);

  return urls;
}

const GalleryCard = React.memo(function GalleryCard({
  block,
  imageUrl,
  imageHeight,
  disabled,
  onClick,
}: {
  block: GalleryBlock;
  imageUrl: string | undefined;
  imageHeight: number;
  disabled: boolean;
  onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <Box
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-label={block.title}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={(e: React.MouseEvent) => {
        // Left click only, matching ButtonComponent.
        if (e.button !== 0 || disabled) return;
        onClick();
      }}
      onKeyDown={(e: React.KeyboardEvent) => {
        if (disabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={(theme) => ({
        display: "flex",
        flexDirection: "column",
        border: `1px solid ${
          hovered && !disabled ? theme.colors.blue[5] : theme.colors.gray[4]
        }`,
        borderRadius: theme.radius.sm,
        overflow: "hidden",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.5 : 1,
        backgroundColor: "#fff",
        transition: "border-color 100ms, box-shadow 100ms",
        boxShadow: hovered && !disabled ? theme.shadows.sm : undefined,
      })}
    >
      {/* Snapshot area. */}
      <Box
        style={{
          height: imageHeight,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#f8f9fa",
        }}
      >
        {imageUrl === undefined ? null : (
          <img
            src={imageUrl}
            alt={block.title}
            draggable={false}
            style={{
              maxWidth: "100%",
              maxHeight: "100%",
              objectFit: "contain",
              display: "block",
            }}
          />
        )}
      </Box>
      {/* White footer: title, then optional subtitle. */}
      <Box style={{ backgroundColor: "#fff", padding: "0.5em 0.6em" }}>
        <Text fz="sm" c="dark" truncate="end">
          {block.title}
        </Text>
        {block.subtitle === null ? null : (
          <Text fz="xs" c="dimmed" truncate="end">
            {block.subtitle}
          </Text>
        )}
      </Box>
    </Box>
  );
});

/** "Create new" tile: a dashed frame with an icon badge and centered text.
 *
 * Fills its grid cell (the grid stretches cards to the row height), so it lines
 * up with the preview cards beside it. */
const GalleryNewCard = React.memo(function GalleryNewCard({
  block,
  imageHeight,
  disabled,
  onClick,
}: {
  block: GalleryBlock;
  imageHeight: number;
  disabled: boolean;
  onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const active = hovered && !disabled;
  // Scale the badge with the block so it reads well in both a full-window
  // gallery and a small docked one.
  const badgeSize = Math.round(Math.min(48, Math.max(26, imageHeight * 0.28)));

  return (
    <Box
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-label={block.title}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={(e: React.MouseEvent) => {
        if (e.button !== 0 || disabled) return;
        onClick();
      }}
      onKeyDown={(e: React.KeyboardEvent) => {
        if (disabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={(theme) => ({
        boxSizing: "border-box",
        height: "100%",
        // Standalone in a row it has no neighbor to stretch to; keep it at
        // least as tall as a snapshot plus a one-line footer.
        minHeight: `calc(${imageHeight}px + 2.4em)`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "0.4em",
        padding: "0.6em",
        border: `2px dashed ${active ? theme.colors.blue[5] : theme.colors.gray[4]}`,
        borderRadius: theme.radius.md,
        backgroundColor: active ? theme.colors.blue[0] : "#fafbfc",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "border-color 120ms, background-color 120ms",
        textAlign: "center",
      })}
    >
      {block._icon_html === null ? null : (
        <Box
          style={(theme) => ({
            width: badgeSize,
            height: badgeSize,
            flexShrink: 0,
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: badgeSize * 0.55,
            color: active ? "#fff" : theme.colors.gray[6],
            backgroundColor: active
              ? theme.colors.blue[5]
              : theme.colors.gray[1],
            transition: "color 120ms, background-color 120ms",
          })}
        >
          <div
            className={centeredIconWrapper}
            dangerouslySetInnerHTML={{ __html: block._icon_html }}
          />
        </Box>
      )}
      <Text
        fz="sm"
        fw={500}
        c={active ? "blue.7" : "gray.7"}
        lineClamp={2}
        style={{ transition: "color 120ms" }}
      >
        {block.title}
      </Text>
      {block.subtitle === null ? null : (
        <Text fz="xs" c="dimmed" lineClamp={2}>
          {block.subtitle}
        </Text>
      )}
    </Box>
  );
});

export default function GalleryComponent({ uuid, props }: GuiGalleryMessage) {
  const { messageSender } = React.useContext(GuiComponentContext)!;
  const imageUrls = useBlockImageUrls(props.blocks);

  const blocks = useMemo(() => props.blocks, [props.blocks]);

  if (!props.visible) return null;

  const inline = props.placement === "inline";

  return (
    <Box
      style={{
        width: "100%",
        // A window gallery owns the viewport; an inline one grows to fit its
        // container (panel tab, folder, control panel) and lets it scroll.
        height: inline ? undefined : "100%",
        overflowY: inline ? undefined : "auto",
      }}
      p={inline ? "xs" : "md"}
    >
      {props.label === "" ? null : (
        <Text fz={inline ? "sm" : "lg"} fw={600} mb="xs">
          {props.label}
        </Text>
      )}
      <Box
        style={{
          display: "grid",
          // auto-fill + minmax lets the browser derive the column count from
          // the available width, so the grid reflows to cover the window
          // without any JS resize handling.
          gridTemplateColumns: `repeat(auto-fill, minmax(${props.block_width}px, 1fr))`,
          gap: props.gap,
          // Stretch so every card in a row shares its height: a "new" tile
          // then matches its neighbors, and subtitle-less cards line up.
          alignItems: "stretch",
        }}
      >
        {blocks.map((block) => {
          const onClick = () =>
            messageSender({
              type: "GuiGalleryClickMessage",
              uuid: uuid,
              block_id: block.block_id,
            });
          return block.kind === "new" ? (
            <GalleryNewCard
              key={block.block_id}
              block={block}
              imageHeight={props.block_height}
              disabled={props.disabled}
              onClick={onClick}
            />
          ) : (
            <GalleryCard
              key={block.block_id}
              block={block}
              imageUrl={imageUrls[block.block_id]}
              imageHeight={props.block_height}
              disabled={props.disabled}
              onClick={onClick}
            />
          );
        })}
      </Box>
    </Box>
  );
}
