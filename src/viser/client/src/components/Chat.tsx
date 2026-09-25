import React, { useContext, useEffect, useRef, useState } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Group,
  Loader,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import {
  IconFile,
  IconMenu2,
  IconPaperclip,
  IconPencil,
  IconPlus,
  IconSend2,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { ErrorBoundary } from "react-error-boundary";
import Markdown from "../Markdown";
import { ViewerContext } from "../ViewerContext";
import { GuiChatMessage, GuiChatSubmitMessage } from "../WebsocketMessages";

type ChatEntry = GuiChatMessage["props"]["messages"][number];
type Upload = GuiChatSubmitMessage["attachments"][number];

/** Total attachment size per submission. The websocket caps messages at 50 MB. */
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
const THUMBNAIL_SIZE = 256;
/** Smallest height the chat shrinks to when filling a short panel. */
const MIN_FILL_HEIGHT = 240;

/** Height that makes the chat fill its container's scroll viewport.
 *
 * Panel bodies (and the control panel) wrap content in a Mantine ScrollArea,
 * so CSS `height: 100%` has nothing definite to resolve against. Instead,
 * measure: the viewport height minus everything else in the scrolled content
 * (padding, sibling components) is the space left for the chat.
 *
 * In a content-sized container (an un-resized floating panel) the viewport
 * equals the content, so this is a fixed point at the current height and the
 * `initial` height is kept. In a fixed-height one (docked or resized panel)
 * the chat grows or shrinks with it, down to MIN_FILL_HEIGHT.
 *
 * Also returns a negative top margin that absorbs the container's top padding
 * when the chat is its first child, so the chat sits flush under the tab strip. */
function useFillHeight(
  ref: React.RefObject<HTMLDivElement | null>,
  initial: number,
  enabled: boolean,
) {
  const [height, setHeight] = useState(initial);
  const [marginTop, setMarginTop] = useState(0);
  useEffect(() => {
    const root = ref.current;
    const viewport = root?.closest<HTMLElement>(".mantine-ScrollArea-viewport");
    const content = viewport?.firstElementChild;
    if (!enabled || !root || !viewport || !(content instanceof HTMLElement))
      return;
    const parent = root.parentElement;
    if (parent !== null && parent.firstElementChild === root) {
      setMarginTop(-(parseFloat(getComputedStyle(parent).paddingTop) || 0));
    }
    const update = () => {
      // Fractional rects, floored once: integer offset/client heights round
      // independently and can overshoot the viewport by a pixel.
      const other =
        content.getBoundingClientRect().height -
        root.getBoundingClientRect().height;
      const target = Math.max(
        MIN_FILL_HEIGHT,
        Math.floor(viewport.getBoundingClientRect().height - other),
      );
      // Tolerance avoids a resize-observer ping-pong on sub-pixel rounding.
      setHeight((h) => (Math.abs(h - target) < 1 ? h : target));
    };
    const observer = new ResizeObserver(update);
    observer.observe(viewport);
    observer.observe(content);
    update();
    return () => observer.disconnect();
  }, [ref, enabled]);
  return { height, marginTop };
}

/** Downscale an image to a small JPEG preview, or null if it can't be decoded. */
async function makeThumbnail(
  file: File,
): Promise<Uint8Array<ArrayBuffer> | null> {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(
      1,
      THUMBNAIL_SIZE / Math.max(bitmap.width, bitmap.height),
    );
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#fff"; // Flatten transparency: JPEG has no alpha.
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.8),
    );
    return blob === null ? null : new Uint8Array(await blob.arrayBuffer());
  } catch {
    return null;
  }
}

/** Blob URL for some image bytes, revoked when the bytes change or on unmount. */
function useBlobUrl(data: Uint8Array<ArrayBuffer> | null, type: string) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (data === null) {
      setUrl(null);
      return;
    }
    const next = URL.createObjectURL(new Blob([data], { type }));
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [data, type]);
  return url;
}

function AttachmentChip({
  name,
  thumbnail,
  onRemove,
}: {
  name: string;
  thumbnail: Uint8Array<ArrayBuffer> | null;
  onRemove?: () => void;
}) {
  const url = useBlobUrl(thumbnail, "image/jpeg");
  return (
    <Box
      title={name}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        maxWidth: url === null ? 180 : undefined,
        padding: url === null ? "2px 6px" : 2,
        border: "1px solid var(--mantine-color-default-border)",
        borderRadius: 6,
        background: "var(--mantine-color-body)",
      }}
    >
      {url === null ? (
        <>
          <IconFile size={14} style={{ flexShrink: 0 }} />
          <Text fz="xs" truncate="end">
            {name}
          </Text>
        </>
      ) : (
        <img
          src={url}
          alt={name}
          style={{
            height: 56,
            maxWidth: 120,
            objectFit: "cover",
            borderRadius: 4,
          }}
        />
      )}
      {onRemove && (
        <ActionIcon
          size="xs"
          variant="subtle"
          color="gray"
          aria-label={`Remove ${name}`}
          onClick={onRemove}
        >
          <IconX size={12} />
        </ActionIcon>
      )}
    </Box>
  );
}

function AssistantText({ text }: { text: string }) {
  return (
    <ErrorBoundary fallback={<Text fz="sm">{text}</Text>}>
      <Box fz="sm" style={{ overflowWrap: "anywhere" }}>
        {/* "md", not MDX: replies are untrusted and must not run code. */}
        <Markdown format="md">{text}</Markdown>
      </Box>
    </ErrorBoundary>
  );
}

const ChatBubble = React.memo(function ChatBubble({
  entry,
}: {
  entry: ChatEntry;
}) {
  const isUser = entry.role === "user";
  return (
    <Stack gap={4} align={isUser ? "flex-end" : "stretch"}>
      {entry.attachments.length > 0 && (
        <Group gap={4} justify={isUser ? "flex-end" : "flex-start"}>
          {entry.attachments.map((a, i) => (
            <AttachmentChip key={i} name={a.name} thumbnail={a._thumbnail} />
          ))}
        </Group>
      )}
      {entry.text !== "" &&
        (isUser ? (
          <Box
            fz="sm"
            px="sm"
            py={6}
            style={{
              maxWidth: "85%",
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              borderRadius: 12,
              background: "var(--mantine-color-default-hover)",
            }}
          >
            {entry.text}
          </Box>
        ) : (
          <Box
            style={
              entry.role === "system"
                ? { opacity: 0.7, fontStyle: "italic" }
                : undefined
            }
          >
            <AssistantText text={entry.text} />
          </Box>
        ))}
    </Stack>
  );
});

function HistoryDrawer({
  props,
  send,
  onClose,
}: {
  props: GuiChatMessage["props"];
  send: (
    action: "new" | "open" | "delete" | "rename",
    conversationId?: string,
    value?: string,
  ) => void;
  onClose: () => void;
}) {
  const [renaming, setRenaming] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const locked = props.busy || props.disabled;
  return (
    <Box
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 2,
        display: "flex",
        flexDirection: "column",
        background: "var(--mantine-color-body)",
      }}
    >
      <Group justify="space-between" px="xs" py={6} wrap="nowrap">
        <Text fw={600} fz="sm">
          Conversations
        </Text>
        <ActionIcon
          variant="subtle"
          color="gray"
          aria-label="Close history"
          onClick={onClose}
        >
          <IconX size={16} />
        </ActionIcon>
      </Group>
      <Box px="xs" pb="xs">
        <Button
          fullWidth
          size="xs"
          variant="default"
          leftSection={<IconPlus size={14} />}
          disabled={locked}
          onClick={() => {
            send("new");
            onClose();
          }}
        >
          New conversation
        </Button>
      </Box>
      <Box style={{ flex: 1, overflowY: "auto" }} px={4}>
        {props.conversations.length === 0 && (
          <Text fz="xs" c="dimmed" ta="center" mt="md">
            No saved conversations yet.
          </Text>
        )}
        {props.conversations.map((c) => {
          const active = c.conversation_id === props.active_conversation_id;
          if (renaming?.id === c.conversation_id) {
            const commit = () => {
              send("rename", c.conversation_id, renaming.title);
              setRenaming(null);
            };
            return (
              <TextInput
                key={c.conversation_id}
                size="xs"
                p={4}
                autoFocus
                value={renaming.title}
                onChange={(e) =>
                  setRenaming({ id: c.conversation_id, title: e.target.value })
                }
                onBlur={commit}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commit();
                  if (e.key === "Escape") setRenaming(null);
                }}
              />
            );
          }
          return (
            <Group
              key={c.conversation_id}
              gap={2}
              wrap="nowrap"
              px={6}
              py={4}
              style={{
                borderRadius: 6,
                cursor: locked ? "default" : "pointer",
                background: active
                  ? "var(--mantine-color-default-hover)"
                  : undefined,
              }}
              onClick={() => {
                if (locked) return;
                if (!active) send("open", c.conversation_id);
                onClose();
              }}
            >
              <Box style={{ flex: 1, minWidth: 0 }}>
                <Text fz="sm" truncate="end">
                  {c.title}
                </Text>
                <Text fz={10} c="dimmed">
                  {new Date(c.updated_at * 1000).toLocaleString()}
                </Text>
              </Box>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                aria-label="Rename conversation"
                onClick={(e) => {
                  e.stopPropagation();
                  setRenaming({ id: c.conversation_id, title: c.title });
                }}
              >
                <IconPencil size={14} />
              </ActionIcon>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                aria-label="Delete conversation"
                disabled={locked}
                onClick={(e) => {
                  e.stopPropagation();
                  if (window.confirm(`Delete "${c.title}"?`)) {
                    send("delete", c.conversation_id);
                  }
                }}
              >
                <IconTrash size={14} />
              </ActionIcon>
            </Group>
          );
        })}
      </Box>
    </Box>
  );
}

export default function ChatComponent({ uuid, props }: GuiChatMessage) {
  const viewer = useContext(ViewerContext)!;
  const [text, setText] = useState("");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const { height, marginTop } = useFillHeight(
    rootRef,
    props.height,
    props.visible,
  );

  // Sent unthrottled: the shared throttled sender coalesces to the latest
  // message, which could drop a submission. Read at call time, since the
  // websocket layer reassigns sendMessage on reconnect.
  const sendMessage: typeof viewer.mutable.current.sendMessage = (message) =>
    viewer.mutable.current.sendMessage(message);
  const sendAction = (
    action: "new" | "open" | "delete" | "rename",
    conversationId = "",
    value = "",
  ) =>
    sendMessage({
      type: "GuiChatActionMessage",
      uuid,
      action,
      conversation_id: conversationId,
      value,
    });

  // Keep the newest message in view.
  const lastId = props.messages[props.messages.length - 1]?.message_id;
  useEffect(() => {
    const el = scrollRef.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [lastId, props.streaming_text, props.busy]);

  const locked = props.busy || props.disabled;

  async function addFiles(files: File[]) {
    if (files.length === 0) return;
    setError(null);
    const added: Upload[] = [];
    let total = uploads.reduce((n, u) => n + u._data.byteLength, 0);
    for (const file of files) {
      total += file.size;
      if (total > MAX_ATTACHMENT_BYTES) {
        setError(
          `Attachments are limited to ${MAX_ATTACHMENT_BYTES >> 20} MB.`,
        );
        break;
      }
      const isImage = file.type.startsWith("image/");
      added.push({
        name: file.name || (isImage ? "pasted-image.png" : "file"),
        mime_type: file.type || "application/octet-stream",
        _data: new Uint8Array(await file.arrayBuffer()),
        _thumbnail: isImage ? await makeThumbnail(file) : null,
      });
    }
    setUploads((prev) => [...prev, ...added]);
  }

  function submit(content: string) {
    if (locked || (content.trim() === "" && uploads.length === 0)) return;
    sendMessage({
      type: "GuiChatSubmitMessage",
      uuid,
      text: content,
      attachments: uploads,
    });
    setText("");
    setUploads([]);
    setError(null);
  }

  if (!props.visible) return null;

  const empty =
    props.messages.length === 0 && props.streaming_text === null && !props.busy;
  const canSend = !locked && (text.trim() !== "" || uploads.length > 0);

  return (
    <Box
      ref={rootRef}
      style={{
        position: "relative",
        width: "100%",
        height,
        marginTop,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background: "var(--mantine-color-body)",
        // The panel already frames its content; only outline drag targets.
        outline: dragOver
          ? "1px dashed var(--mantine-primary-color-filled)"
          : undefined,
        outlineOffset: -1,
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        if (e.dataTransfer.files.length === 0) return;
        e.preventDefault();
        setDragOver(false);
        void addFiles(Array.from(e.dataTransfer.files));
      }}
    >
      {/* Header. */}
      <Group
        gap={4}
        px={6}
        py={4}
        wrap="nowrap"
        style={{ background: "var(--mantine-color-default-hover)" }}
      >
        <Tooltip label="Conversations" openDelay={500}>
          <ActionIcon
            variant="subtle"
            color="gray"
            aria-label="Show conversations"
            onClick={() => setHistoryOpen(true)}
          >
            <IconMenu2 size={18} />
          </ActionIcon>
        </Tooltip>
        <Text fz="sm" fw={600} truncate="end" style={{ flex: 1 }}>
          {props.label}
        </Text>
        <Tooltip label="New conversation" openDelay={500}>
          <ActionIcon
            variant="subtle"
            color="gray"
            aria-label="New conversation"
            disabled={locked || props.messages.length === 0}
            onClick={() => sendAction("new")}
          >
            <IconPlus size={18} />
          </ActionIcon>
        </Tooltip>
      </Group>

      {/* Messages, or the greeting while the conversation is empty. */}
      <Box
        ref={scrollRef}
        px="sm"
        py="sm"
        style={{ flex: 1, overflowY: "auto" }}
      >
        {empty ? (
          <Stack gap="xs" h="100%">
            <Title order={3}>{props.greeting}</Title>
            {props.subtitle !== "" && <Text fz="sm">{props.subtitle}</Text>}
            <Stack gap={6} align="flex-start" mt="xs">
              {props.suggestions.map((s) => (
                <Button
                  key={s}
                  size="xs"
                  radius="xl"
                  variant="default"
                  disabled={locked}
                  onClick={() => submit(s)}
                >
                  {s}
                </Button>
              ))}
            </Stack>
            {props.disclaimer !== null && (
              <Box mt="auto">
                <Text fz="sm" fw={600}>
                  Just FYI…
                </Text>
                <Text fz="xs" c="dimmed">
                  {props.disclaimer}
                </Text>
              </Box>
            )}
          </Stack>
        ) : (
          <Stack gap="md">
            {props.messages.map((m) => (
              <ChatBubble key={m.message_id} entry={m} />
            ))}
            {props.streaming_text !== null && props.streaming_text !== "" ? (
              <AssistantText text={props.streaming_text} />
            ) : (
              props.busy && <Loader type="dots" size="sm" color="gray" />
            )}
          </Stack>
        )}
      </Box>

      {/* Input. */}
      <Box
        px={6}
        py={6}
        style={{ background: "var(--mantine-color-default-hover)" }}
      >
        {uploads.length > 0 && (
          <Group gap={4} mb={6}>
            {uploads.map((u, i) => (
              <AttachmentChip
                key={i}
                name={u.name}
                thumbnail={u._thumbnail}
                onRemove={() =>
                  setUploads((prev) => prev.filter((_, j) => j !== i))
                }
              />
            ))}
          </Group>
        )}
        {error !== null && (
          <Text fz="xs" c="red" mb={4}>
            {error}
          </Text>
        )}
        <Group gap={4} wrap="nowrap" align="flex-end">
          <Tooltip label="Attach files" openDelay={500}>
            <ActionIcon
              variant="subtle"
              color="gray"
              aria-label="Attach files"
              disabled={props.disabled}
              onClick={() => fileInputRef.current?.click()}
              mb={4}
            >
              <IconPaperclip size={18} />
            </ActionIcon>
          </Tooltip>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              void addFiles(Array.from(e.target.files ?? []));
              e.target.value = ""; // Allow re-selecting the same file.
            }}
          />
          <Textarea
            style={{ flex: 1 }}
            size="sm"
            autosize
            minRows={2}
            maxRows={6}
            placeholder={props.placeholder}
            disabled={props.disabled}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter inserts a newline. Skip while an IME
              // composition is active, where Enter confirms the composition.
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                submit(text);
              }
            }}
            onPaste={(e) => {
              const files = Array.from(e.clipboardData.files);
              if (files.length === 0) return;
              e.preventDefault();
              void addFiles(files);
            }}
          />
          <ActionIcon
            size="lg"
            variant="filled"
            aria-label="Send"
            disabled={!canSend}
            onClick={() => submit(text)}
            mb={2}
          >
            <IconSend2 size={18} />
          </ActionIcon>
        </Group>
      </Box>

      {historyOpen && (
        <HistoryDrawer
          props={props}
          send={sendAction}
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </Box>
  );
}
