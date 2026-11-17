import React from "react";
import {
  Box,
  Divider,
  Menu,
  ScrollArea,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import {
  IconCaretDown,
  IconCaretRight,
  IconChevronDown,
  IconChevronUp,
  IconEye,
  IconEyeOff,
  IconGripHorizontal,
  IconPencil,
  IconTrash,
} from "@tabler/icons-react";
import { ViewerContext } from "../ViewerContext";
import { GuiSceneTreeMessage } from "../WebsocketMessages";
import SceneTreeTable from "../ControlPanel/SceneTreeTable";
import {
  editIconWrapper,
  tableHierarchyLine,
  tableRow,
  tableWrapper,
} from "../ControlPanel/SceneTreeTable.css";
import { centeredIconWrapper } from "./ComponentStyles.css";

type SceneTreeItem = GuiSceneTreeMessage["props"]["items"][number];
type SceneTreeMenuEntry = GuiSceneTreeMessage["props"]["menu"][number];
type SceneTreeAction = "click" | "visibility" | "rename" | "remove" | "menu";

/** Shared by every row of one tree: the tree's identity plus the two ways a
 * row talks back -- an optimistic local patch and the message to the server
 * (which applies the change and rebroadcasts it to every client). */
interface SceneTreeRowContext {
  childrenByParent: Map<string | null, SceneTreeItem[]>;
  selectedId: string | null;
  disabled: boolean;
  /** Custom right-click menu entries, for every item or scoped to one. */
  menu: readonly SceneTreeMenuEntry[];
  act: (item: SceneTreeItem, action: SceneTreeAction, value?: string) => void;
}

const ROW_ICON_STYLE = {
  width: "1.5em",
  height: "1.5em",
  display: "block",
} as const;

const CARET_STYLE = {
  height: "1em",
  width: "1em",
  transform: "translateY(0.1em)",
} as const;

/** Group items by parent. Items whose parent is unknown are shown at the top
 * level rather than dropped. */
function groupByParent(items: readonly SceneTreeItem[]) {
  const ids = new Set(items.map((item) => item.item_id));
  const out = new Map<string | null, SceneTreeItem[]>();
  for (const item of items) {
    const key =
      item.parent_id !== null && ids.has(item.parent_id)
        ? item.parent_id
        : null;
    const list = out.get(key);
    if (list === undefined) out.set(key, [item]);
    else list.push(item);
  }
  return out;
}

/** One level of an item's custom menu; an entry with children of its own
 * opens a submenu instead of firing a click. */
function MenuEntries({
  parentId,
  childrenOf,
  onClick,
}: {
  parentId: string | null;
  childrenOf: Map<string | null, SceneTreeMenuEntry[]>;
  onClick: (menuId: string) => void;
}) {
  return (childrenOf.get(parentId) ?? []).map((entry) => {
    const icon =
      entry._icon_html === null ? undefined : (
        <Box
          className={centeredIconWrapper}
          dangerouslySetInnerHTML={{ __html: entry._icon_html }}
        />
      );
    if (!childrenOf.has(entry.menu_id))
      return (
        <Menu.Item
          key={entry.menu_id}
          leftSection={icon}
          onClick={() => onClick(entry.menu_id)}
        >
          {entry.label}
        </Menu.Item>
      );
    return (
      <Menu.Sub key={entry.menu_id}>
        <Menu.Sub.Target>
          <Menu.Sub.Item leftSection={icon}>{entry.label}</Menu.Sub.Item>
        </Menu.Sub.Target>
        <Menu.Sub.Dropdown miw="9em">
          <MenuEntries
            parentId={entry.menu_id}
            childrenOf={childrenOf}
            onClick={onClick}
          />
        </Menu.Sub.Dropdown>
      </Menu.Sub>
    );
  });
}

/** All ids in the subtree rooted at `rootId`, including the root. */
function subtreeIds(
  rootId: string,
  childrenByParent: Map<string | null, SceneTreeItem[]>,
) {
  const out = new Set<string>([rootId]);
  const stack = [rootId];
  while (stack.length > 0) {
    for (const child of childrenByParent.get(stack.pop()!) ?? []) {
      if (!out.has(child.item_id)) {
        out.add(child.item_id);
        stack.push(child.item_id);
      }
    }
  }
  return out;
}

const SceneTreeItemRow = React.memo(function SceneTreeItemRow({
  item,
  depth,
  parentVisible,
  ctx,
}: {
  item: SceneTreeItem;
  depth: number;
  parentVisible: boolean;
  ctx: SceneTreeRowContext;
}) {
  const children = ctx.childrenByParent.get(item.item_id) ?? [];
  const expandable = children.length > 0;

  // Expansion is local per client, seeded from (and reset by) the server prop.
  const [expanded, setExpanded] = React.useState(item.expanded);
  React.useEffect(() => setExpanded(item.expanded), [item.expanded]);

  const [renaming, setRenaming] = React.useState(false);
  const [draftName, setDraftName] = React.useState(item.name);
  const [menuPos, setMenuPos] = React.useState<{ x: number; y: number } | null>(
    null,
  );

  const effectiveVisible = parentVisible && item.visible;
  const selected = ctx.selectedId === item.item_id;
  // Custom entries offered on this item, grouped by submenu.
  const menuChildren = React.useMemo(() => {
    const out = new Map<string | null, SceneTreeMenuEntry[]>();
    for (const entry of ctx.menu) {
      if (entry.item_id !== null && entry.item_id !== item.item_id) continue;
      const list = out.get(entry.parent_menu_id);
      if (list === undefined) out.set(entry.parent_menu_id, [entry]);
      else list.push(entry);
    }
    return out;
  }, [ctx.menu, item.item_id]);
  const hasCustomMenu = menuChildren.has(null);
  const hasBuiltinMenu = item.editable || item.removable;
  const hasMenu = !ctx.disabled && (hasCustomMenu || hasBuiltinMenu);
  const VisibleIcon = item.visible ? IconEye : IconEyeOff;

  // Unmounting the input fires a blur after Enter/Escape already ended the
  // edit; this guard keeps that blur from committing twice (or committing a
  // cancelled edit).
  const renameActive = React.useRef(false);
  const startRename = () => {
    if (ctx.disabled || !item.editable) return;
    renameActive.current = true;
    setDraftName(item.name);
    setRenaming(true);
  };
  const endRename = (commit: boolean) => {
    if (!renameActive.current) return;
    renameActive.current = false;
    setRenaming(false);
    const name = draftName.trim();
    if (commit && name !== "" && name !== item.name)
      ctx.act(item, "rename", name);
  };

  return (
    <>
      <Box
        className={tableRow}
        data-scene-tree-item={item.item_id}
        style={{
          cursor: ctx.disabled ? undefined : "pointer",
          ...(selected && {
            backgroundColor: "var(--mantine-primary-color-light)",
          }),
        }}
        onClick={() => {
          if (!ctx.disabled && !renaming) ctx.act(item, "click");
        }}
        onContextMenu={(evt) => {
          if (!hasMenu) return;
          evt.preventDefault();
          setMenuPos({ x: evt.clientX, y: evt.clientY });
        }}
      >
        {new Array(depth).fill(null).map((_, i) => (
          <Box className={tableHierarchyLine} key={i} />
        ))}
        <Box
          style={{ opacity: expandable ? 0.7 : 0.1 }}
          onClick={(evt) => {
            evt.stopPropagation();
            if (expandable) setExpanded((x) => !x);
          }}
        >
          {expanded && expandable ? (
            <IconCaretDown style={CARET_STYLE} />
          ) : (
            <IconCaretRight style={CARET_STYLE} />
          )}
        </Box>
        <Box style={{ width: "1.5em", height: "1.5em", flexShrink: 0 }}>
          <Tooltip label="Toggle visibility" openDelay={400}>
            <VisibleIcon
              aria-label={`Toggle visibility of ${item.name}`}
              style={{
                ...ROW_ICON_STYLE,
                cursor: ctx.disabled ? undefined : "pointer",
                opacity: effectiveVisible ? 0.85 : 0.25,
              }}
              onClick={(evt) => {
                evt.stopPropagation();
                if (!ctx.disabled)
                  ctx.act(item, "visibility", item.visible ? "false" : "true");
              }}
            />
          </Tooltip>
        </Box>
        {item._icon_html === null ? null : (
          <Box
            className={centeredIconWrapper}
            style={{
              flexShrink: 0,
              opacity: effectiveVisible ? 0.8 : 0.35,
            }}
            dangerouslySetInnerHTML={{ __html: item._icon_html }}
          />
        )}
        {renaming ? (
          <TextInput
            size="xs"
            autoFocus
            value={draftName}
            onChange={(evt) => setDraftName(evt.currentTarget.value)}
            onClick={(evt) => evt.stopPropagation()}
            onBlur={() => endRename(true)}
            onKeyDown={(evt) => {
              if (evt.key === "Enter") endRename(true);
              else if (evt.key === "Escape") endRename(false);
            }}
            styles={{ input: { height: "1.625rem", minHeight: "1.625rem" } }}
            style={{ flexGrow: 1 }}
          />
        ) : (
          <Box
            style={{
              flexGrow: 1,
              userSelect: "none",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              opacity: effectiveVisible ? 1 : 0.5,
              fontWeight: selected ? 500 : undefined,
            }}
            onDoubleClick={(evt) => {
              evt.stopPropagation();
              startRename();
            }}
          >
            {item.name}
          </Box>
        )}
        {!renaming && !ctx.disabled && item.editable ? (
          <Box className={editIconWrapper} style={{ flexShrink: 0 }}>
            <Tooltip label="Rename" openDelay={400}>
              <IconPencil
                aria-label={`Rename ${item.name}`}
                style={{ ...ROW_ICON_STYLE, width: "1.25em", height: "1.25em" }}
                onClick={(evt) => {
                  evt.stopPropagation();
                  startRename();
                }}
              />
            </Tooltip>
          </Box>
        ) : null}
        {!renaming && !ctx.disabled && item.removable ? (
          <Box
            className={editIconWrapper}
            style={{ flexShrink: 0, marginRight: "0.25em" }}
          >
            <Tooltip label="Delete" openDelay={400}>
              <IconTrash
                aria-label={`Delete ${item.name}`}
                style={{ ...ROW_ICON_STYLE, width: "1.25em", height: "1.25em" }}
                onClick={(evt) => {
                  evt.stopPropagation();
                  ctx.act(item, "remove");
                }}
              />
            </Tooltip>
          </Box>
        ) : null}
      </Box>
      {menuPos === null ? null : (
        <Menu
          opened
          onChange={(opened) => {
            if (!opened) setMenuPos(null);
          }}
          position="bottom-start"
          offset={2}
          withinPortal
          zIndex={1000}
        >
          <Menu.Target>
            {/* Zero-size anchor at the cursor: the menu opens where the user
            right-clicked, not at the row's edge. */}
            <Box
              style={{
                position: "fixed",
                left: menuPos.x,
                top: menuPos.y,
                width: 0,
                height: 0,
              }}
            />
          </Menu.Target>
          <Menu.Dropdown miw="9em">
            <MenuEntries
              parentId={null}
              childrenOf={menuChildren}
              onClick={(menuId) => ctx.act(item, "menu", menuId)}
            />
            {hasCustomMenu && hasBuiltinMenu ? <Menu.Divider /> : null}
            {item.editable ? (
              <Menu.Item
                leftSection={<IconPencil size="0.9rem" />}
                onClick={startRename}
              >
                Rename
              </Menu.Item>
            ) : null}
            {item.removable ? (
              <Menu.Item
                color="red"
                leftSection={<IconTrash size="0.9rem" />}
                onClick={() => ctx.act(item, "remove")}
              >
                Delete
              </Menu.Item>
            ) : null}
          </Menu.Dropdown>
        </Menu>
      )}
      {expanded
        ? children.map((child) => (
            <SceneTreeItemRow
              key={child.item_id}
              item={child}
              depth={depth + 1}
              parentVisible={effectiveVisible}
              ctx={ctx}
            />
          ))
        : null}
    </>
  );
});

/** Custom items followed by the viser scene graph. */
function SceneTreeBody({ uuid, props }: GuiSceneTreeMessage) {
  const viewer = React.useContext(ViewerContext)!;
  const items = props.items;
  const childrenByParent = React.useMemo(() => groupByParent(items), [items]);

  // Latest items for `act`, so its identity stays stable across item updates
  // (rows are memoized on `ctx`).
  const itemsRef = React.useRef(items);
  itemsRef.current = items;
  const childrenRef = React.useRef(childrenByParent);
  childrenRef.current = childrenByParent;

  const act = React.useCallback(
    (item: SceneTreeItem, action: SceneTreeAction, value: string = "") => {
      // Optimistic local update; the server's rebroadcast confirms (or, for a
      // rejected rename, reverts) it.
      const patch = viewer.guiActions.updateGuiProps;
      if (action === "click") {
        patch(uuid, { selected_id: item.item_id });
      } else if (action === "visibility" || action === "rename") {
        const changes =
          action === "visibility"
            ? { visible: value === "true" }
            : { name: value };
        patch(uuid, {
          items: itemsRef.current.map((entry) =>
            entry.item_id === item.item_id ? { ...entry, ...changes } : entry,
          ),
        });
      } else if (action === "remove") {
        const doomed = subtreeIds(item.item_id, childrenRef.current);
        patch(uuid, {
          items: itemsRef.current.filter((entry) => !doomed.has(entry.item_id)),
        });
      }
      // Sent unthrottled: these are discrete events, and the throttled sender
      // coalesces to the latest message.
      viewer.mutable.current.sendMessage({
        type: "GuiSceneTreeActionMessage",
        uuid,
        item_id: item.item_id,
        action,
        value,
      });
    },
    [viewer, uuid],
  );

  const ctx = React.useMemo<SceneTreeRowContext>(
    () => ({
      childrenByParent,
      selectedId: props.selected_id,
      disabled: props.disabled,
      menu: props.menu,
      act,
    }),
    [childrenByParent, props.selected_id, props.disabled, props.menu, act],
  );

  const roots = childrenByParent.get(null) ?? [];
  return (
    <ScrollArea.Autosize mah={props.max_height} scrollbarSize={6} type="auto">
      {roots.length === 0 ? null : (
        <Box className={tableWrapper}>
          {roots.map((item) => (
            <SceneTreeItemRow
              key={item.item_id}
              item={item}
              depth={0}
              parentVisible
              ctx={ctx}
            />
          ))}
        </Box>
      )}
      {props.show_scene_nodes && roots.length > 0 ? (
        <Divider my="0.25em" />
      ) : null}
      {props.show_scene_nodes ? <SceneTreeTable /> : null}
    </ScrollArea.Autosize>
  );
}

/** Floating, draggable, collapsible scene tree panel over the viewport.
 *
 * Positioned inside SceneTreeOverlay (which fills the viewer area). It starts
 * anchored to a corner; the first title-bar drag switches it to explicit
 * coordinates. Placement is client-local, like the playback scene panel. */
function SceneTreeWindow(conf: GuiSceneTreeMessage) {
  const { props } = conf;
  const paperRef = React.useRef<HTMLDivElement | null>(null);
  const [dragPos, setDragPos] = React.useState<{
    left: number;
    top: number;
  } | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const dragOffset = React.useRef<{ dx: number; dy: number } | null>(null);

  const [collapsed, setCollapsed] = React.useState(props.collapsed);
  React.useEffect(() => setCollapsed(props.collapsed), [props.collapsed]);

  const opacityPct = Math.round(props.background_opacity * 100);
  const background = `color-mix(in srgb, var(--mantine-color-body) ${opacityPct}%, transparent)`;
  // Over a see-through background, a soft halo in the body color keeps text
  // legible against whatever the canvas shows behind it.
  const textShadow =
    props.background_opacity < 0.5
      ? "0 0 2px var(--mantine-color-body), 0 0 4px var(--mantine-color-body)"
      : undefined;

  const [vertical, horizontal] = props.position.split("-") as [
    "top" | "bottom",
    "left" | "right",
  ];
  const anchor =
    dragPos === null
      ? { [vertical]: "1em", [horizontal]: "1em" }
      : { left: dragPos.left, top: dragPos.top };

  const clamp = (value: number, lo: number, hi: number) =>
    Math.min(Math.max(value, lo), Math.max(lo, hi));

  const endDrag = () => {
    dragOffset.current = null;
    setDragging(false);
  };

  return (
    <Box
      ref={paperRef}
      data-scene-tree-panel={conf.uuid}
      style={{
        position: "absolute",
        ...anchor,
        width: props.width,
        maxWidth: "calc(100% - 2em)",
        pointerEvents: "auto",
        backgroundColor: background,
        borderRadius: "var(--mantine-radius-xs)",
        boxShadow:
          props.background_opacity > 0
            ? "0.1em 0 1em 0 rgba(0,0,0,0.1)"
            : undefined,
        textShadow,
        overflow: "hidden",
      }}
    >
      <Box
        onPointerDown={(ev) => {
          // The collapse button handles its own clicks.
          if ((ev.target as HTMLElement).closest("[data-scene-tree-collapse]"))
            return;
          const rect = paperRef.current!.getBoundingClientRect();
          dragOffset.current = {
            dx: ev.clientX - rect.left,
            dy: ev.clientY - rect.top,
          };
          setDragging(true);
          ev.currentTarget.setPointerCapture(ev.pointerId);
        }}
        onPointerMove={(ev) => {
          const paper = paperRef.current;
          if (dragOffset.current === null || paper === null) return;
          const parent = paper.offsetParent ?? document.body;
          const parentRect = parent.getBoundingClientRect();
          const rect = paper.getBoundingClientRect();
          setDragPos({
            left: clamp(
              ev.clientX - parentRect.left - dragOffset.current.dx,
              0,
              parentRect.width - rect.width,
            ),
            top: clamp(
              ev.clientY - parentRect.top - dragOffset.current.dy,
              0,
              parentRect.height - rect.height,
            ),
          });
        }}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        fz="sm"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5em",
          padding: "0.375em 0.5em 0.375em 0.625em",
          fontWeight: 500,
          cursor: dragging ? "grabbing" : "grab",
          userSelect: "none",
          // Touch drags move the panel instead of scrolling the page.
          touchAction: "none",
        }}
      >
        <IconGripHorizontal
          size="1em"
          aria-hidden
          style={{ opacity: 0.4, flexShrink: 0 }}
        />
        <Tooltip label={props.hint} disabled={props.hint === null}>
          <Box
            style={{
              flexGrow: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {props.label}
          </Box>
        </Tooltip>
        <Tooltip label={collapsed ? "Expand" : "Collapse"} openDelay={400}>
          <Box
            data-scene-tree-collapse
            role="button"
            aria-label={collapsed ? "Expand scene tree" : "Collapse scene tree"}
            onClick={() => setCollapsed((x) => !x)}
            style={{ cursor: "pointer", display: "flex", opacity: 0.7 }}
          >
            {collapsed ? (
              <IconChevronDown size="1em" />
            ) : (
              <IconChevronUp size="1em" />
            )}
          </Box>
        </Tooltip>
      </Box>
      {collapsed ? null : (
        <Box
          style={{
            borderTop:
              "1px solid color-mix(in srgb, var(--mantine-color-default-border) 60%, transparent)",
            padding: "0.1em 0",
          }}
        >
          <SceneTreeBody {...conf} />
        </Box>
      )}
    </Box>
  );
}

/** Scene tree component: a floating panel ("window" placement, drawn by
 * SceneTreeOverlay) or inline in its container. */
export default function SceneTreePanelComponent(conf: GuiSceneTreeMessage) {
  const { props } = conf;
  if (!props.visible) return null;
  if (props.placement === "window") return <SceneTreeWindow {...conf} />;
  return (
    <Box pb="0.5em" px="xs">
      {props.label === "" ? null : (
        <Tooltip
          label={props.hint}
          disabled={props.hint === null}
          position="top-start"
        >
          <Text fz="sm" fw={500}>
            {props.label}
          </Text>
        </Tooltip>
      )}
      <SceneTreeBody {...conf} />
    </Box>
  );
}
