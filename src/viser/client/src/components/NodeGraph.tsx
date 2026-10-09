import "@xyflow/react/dist/style.css";

import React, { useContext, useMemo, useRef, useState } from "react";
import {
  Box,
  Checkbox,
  Menu,
  NumberInput,
  Paper,
  Loader,
  Text,
  TextInput,
  ThemeIcon,
  Tooltip,
  useComputedColorScheme,
} from "@mantine/core";
import { IconCheck, IconPencil, IconTrash, IconX } from "@tabler/icons-react";
import {
  Background,
  Connection,
  Controls,
  Edge,
  Handle,
  IsValidConnection,
  MiniMap,
  Node,
  NodeChange,
  NodeProps,
  OnBeforeDelete,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import { ViewerContext } from "../ViewerContext";
import { GuiNodeGraphMessage, Message } from "../WebsocketMessages";
import { htmlIconWrapper } from "./ComponentStyles.css";
import { useFillHeight } from "./useFillHeight";

type NodeGraphProps = GuiNodeGraphMessage["props"];
type NodeTypeSpec = NodeGraphProps["node_types"][number];
type PortSpec = NodeTypeSpec["inputs"][number];
type InputValue = boolean | number | string;
type NodeStatus = NodeGraphProps["node_status"][string];
type ViserFlowNode = Node<
  {
    nodeType: NodeTypeSpec;
    values: { [port: string]: InputValue };
    /** Input ports with a wire plugged in; their value fields are hidden. */
    wired: string[];
    status: NodeStatus | null;
  },
  "viser"
>;
type CanvasMenuItem = NodeGraphProps["canvas_menu"][number];
/** Open context menu, anchored at a point relative to the canvas. For the
 * canvas menu, `position` is the right-clicked point in node coordinates. */
type MenuState = { x: number; y: number } & (
  | { kind: "node"; node: ViserFlowNode }
  | { kind: "canvas"; position: [number, number] }
);

/** Smallest height the canvas shrinks to when filling a short panel. */
const MIN_FILL_HEIGHT = 200;
const ROW_HEIGHT_EM = 1.7;
/** Rows holding a value field are a little taller. */
const FIELD_ROW_HEIGHT_EM = 2.1;

/** Sends a value the user entered in a node's input field. */
const SetInputValueContext = React.createContext<
  (nodeId: string, port: string, value: InputValue) => void
>(() => {});

/** Mirrors `dtypes_compatible` in viser/_node_graph.py. */
function dtypesCompatible(outputDtype: string, inputDtype: string) {
  return (
    outputDtype === inputDtype || outputDtype === "any" || inputDtype === "any"
  );
}

/** A stable color per dtype, so matching ports are recognizable at a glance. */
function dtypeColor(dtype: string) {
  if (dtype === "any") return "var(--mantine-color-gray-6)";
  let hash = 0;
  for (let i = 0; i < dtype.length; i++)
    hash = (hash * 31 + dtype.charCodeAt(i)) | 0;
  return `hsl(${Math.abs(hash) % 360}, 65%, 50%)`;
}

function PortHandle({
  port,
  type,
}: {
  port: PortSpec;
  type: "source" | "target";
}) {
  return (
    <Handle
      type={type}
      id={port.name}
      position={type === "target" ? Position.Left : Position.Right}
      title={`${port.label} (${port.dtype})`}
      style={{
        width: "0.6em",
        height: "0.6em",
        background: dtypeColor(port.dtype),
        border: "1px solid var(--mantine-color-body)",
      }}
    />
  );
}

const fieldStyles = {
  input: { height: "1.7em", minHeight: "1.7em", paddingInline: "0.4em" },
};

/** Field editing the value of an unwired input. Text and numbers are sent
 * when the field loses focus or Enter is pressed, a checkbox at once. */
function InputValueField({
  kind,
  value,
  label,
  onCommit,
}: {
  kind: NonNullable<PortSpec["value_kind"]>;
  value: InputValue;
  label: string;
  onCommit: (value: InputValue) => void;
}) {
  const [draft, setDraft] = useState<InputValue>(value);
  // Follow the server, e.g. after another client's edit.
  React.useEffect(() => setDraft(value), [value]);
  const commit = () => {
    const valid =
      kind === "str" ? typeof draft === "string" : typeof draft === "number";
    if (valid && draft !== value) onCommit(draft);
    else setDraft(value);
  };
  const blurOnEnter = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") event.currentTarget.blur();
  };
  if (kind === "bool")
    return (
      <Checkbox
        size="xs"
        aria-label={label}
        checked={value === true}
        onChange={(event) => onCommit(event.currentTarget.checked)}
      />
    );
  if (kind === "str")
    return (
      <TextInput
        size="xs"
        w="7em"
        aria-label={label}
        styles={fieldStyles}
        value={String(draft)}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onBlur={commit}
        onKeyDown={blurOnEnter}
      />
    );
  return (
    <NumberInput
      size="xs"
      w="5.5em"
      aria-label={label}
      styles={fieldStyles}
      hideControls
      allowDecimal={kind === "float"}
      value={typeof draft === "boolean" ? "" : draft}
      onChange={setDraft}
      onBlur={commit}
      onKeyDown={blurOnEnter}
    />
  );
}

/** Spinner while the node's work runs, then a success or error mark. Hovering
 * shows the status message. */
function StatusMark({ status }: { status: NodeStatus }) {
  const mark =
    status.state === "running" ? (
      <Loader size={13} color="white" aria-label="Running" />
    ) : (
      <ThemeIcon
        size={16}
        radius="xl"
        color={status.state === "success" ? "green" : "red"}
        // Stands out on headers of any color.
        style={{ border: "1.5px solid white" }}
        aria-label={status.state === "success" ? "Succeeded" : "Failed"}
      >
        {status.state === "success" ? (
          <IconCheck size={11} stroke={3} />
        ) : (
          <IconX size={11} stroke={3} />
        )}
      </ThemeIcon>
    );
  return (
    <Tooltip
      label={status.message}
      disabled={status.message === null}
      zIndex={1000}
      multiline
      maw="20em"
    >
      <Box style={{ display: "flex" }}>{mark}</Box>
    </Tooltip>
  );
}

/** Generic node: a header with the type label, then one row per port, inputs
 * on the left and outputs on the right. Unwired inputs with a value show a
 * field to edit it. Every node type renders through this one component, so
 * `nodeTypes` stays a module-level constant. */
const ViserNode = React.memo(function ViserNode({
  id,
  data,
  selected,
}: NodeProps<ViserFlowNode>) {
  const { nodeType, values, wired, status } = data;
  const setInputValue = useContext(SetInputValueContext);
  const accent = nodeType.color ?? "var(--mantine-primary-color-filled)";
  const rows = Math.max(nodeType.inputs.length, nodeType.outputs.length);
  return (
    <Paper
      withBorder
      radius="sm"
      shadow={selected ? "md" : "xs"}
      style={{
        minWidth: "10em",
        fontSize: "0.8125rem",
        borderColor:
          status?.state === "error"
            ? "var(--mantine-color-red-6)"
            : selected
              ? accent
              : undefined,
      }}
    >
      <Box
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "0.75em",
          background: accent,
          color: "white",
          padding: "0.25em 0.5em 0.25em 0.75em",
          minHeight: "1.9em",
          fontWeight: 600,
          borderTopLeftRadius: "inherit",
          borderTopRightRadius: "inherit",
        }}
      >
        {nodeType.label}
        {status !== null && <StatusMark status={status} />}
      </Box>
      <Box py="0.3em">
        {Array.from({ length: rows }, (_, i) => {
          const input = nodeType.inputs[i];
          const output = nodeType.outputs[i];
          const field =
            input?.value_kind != null &&
            !wired.includes(input.name) &&
            values[input.name] !== undefined ? (
              <InputValueField
                kind={input.value_kind}
                value={values[input.name]}
                label={input.label}
                onCommit={(value) => setInputValue(id, input.name, value)}
              />
            ) : null;
          return (
            // Handles are absolutely positioned; a relative row centers each
            // one on its label instead of on the whole node.
            <Box
              key={i}
              style={{
                position: "relative",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "1em",
                height: `${field === null ? ROW_HEIGHT_EM : FIELD_ROW_HEIGHT_EM}em`,
                padding: "0 0.75em",
              }}
            >
              <Box
                style={{ display: "flex", alignItems: "center", gap: "0.5em" }}
              >
                <Text size="xs">{input?.label ?? ""}</Text>
                {field !== null && (
                  // "nodrag"/"nowheel": typing and scrolling stay in the
                  // field. Double and right clicks too, instead of opening
                  // the node's settings or menu.
                  <Box
                    className="nodrag nowheel"
                    onDoubleClick={(event) => event.stopPropagation()}
                    onContextMenu={(event) => event.stopPropagation()}
                  >
                    {field}
                  </Box>
                )}
              </Box>
              <Text size="xs">{output?.label ?? ""}</Text>
              {input !== undefined && <PortHandle port={input} type="target" />}
              {output !== undefined && (
                <PortHandle port={output} type="source" />
              )}
            </Box>
          );
        })}
      </Box>
    </Paper>
  );
});

const nodeTypes = { viser: ViserNode };
// Hides the "React Flow" attribution link (allowed by the MIT license).
const proOptions = { hideAttribution: true };

function minimapNodeColor(node: ViserFlowNode) {
  return node.data.nodeType.color ?? "var(--mantine-primary-color-filled)";
}

function toFlowNodes(
  props: NodeGraphProps,
  previous: ViserFlowNode[],
): ViserFlowNode[] {
  const typeFromName = new Map(props.node_types.map((t) => [t.type_name, t]));
  const previousFromId = new Map(previous.map((n) => [n.id, n]));
  const wiredFromId = new Map<string, string[]>();
  for (const edge of props.edges)
    wiredFromId.set(edge.target, [
      ...(wiredFromId.get(edge.target) ?? []),
      edge.target_port,
    ]);
  return props.nodes.flatMap((node) => {
    const nodeType = typeFromName.get(node.type_name);
    if (nodeType === undefined) return [];
    const prev = previousFromId.get(node.node_id);
    const data = {
      nodeType,
      values: node.values,
      status: props.node_status[node.node_id] ?? null,
      wired: wiredFromId.get(node.node_id) ?? [],
    };
    return [
      {
        // Keeps local state: selection, and the measured size -- without it
        // the node is hidden until React Flow measures it again.
        ...prev,
        id: node.node_id,
        type: "viser",
        // A node being dragged follows the pointer, not the server.
        position: prev?.dragging
          ? prev.position
          : { x: node.position[0], y: node.position[1] },
        // Unchanged data keeps its identity, so the memoized node skips
        // rendering.
        data:
          prev !== undefined && sameData(prev.data, data) ? prev.data : data,
      },
    ];
  });
}

function sameData(a: ViserFlowNode["data"], b: ViserFlowNode["data"]) {
  const keys = Object.keys(b.values);
  return (
    a.nodeType === b.nodeType &&
    a.status?.state === b.status?.state &&
    a.status?.message === b.status?.message &&
    a.wired.join("\n") === b.wired.join("\n") &&
    Object.keys(a.values).length === keys.length &&
    keys.every((k) => a.values[k] === b.values[k])
  );
}

function toFlowEdges(props: NodeGraphProps, previous: Edge[]): Edge[] {
  const selected = new Set(previous.filter((e) => e.selected).map((e) => e.id));
  return props.edges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source,
    sourceHandle: edge.source_port,
    target: edge.target,
    targetHandle: edge.target_port,
    selected: selected.has(edge.edge_id),
  }));
}

function menuIcon(iconHtml: string | null) {
  return iconHtml === null ? undefined : (
    <div
      className={htmlIconWrapper}
      dangerouslySetInnerHTML={{ __html: iconHtml }}
    />
  );
}

/** A context menu opened at the cursor. Keeps the last state while the menu
 * animates closed. */
function ContextMenu({
  menu,
  onClose,
  children,
}: {
  menu: MenuState | null;
  onClose: () => void;
  children: (shown: MenuState) => React.ReactNode;
}) {
  const lastMenu = useRef(menu);
  if (menu !== null) lastMenu.current = menu;
  const shown = lastMenu.current;
  if (shown === null) return null;
  return (
    <Menu
      opened={menu !== null}
      onChange={(opened) => !opened && onClose()}
      position="bottom-start"
      offset={2}
      zIndex={1000}
      shadow="md"
      withinPortal
    >
      <Menu.Target>
        <Box
          style={{
            position: "absolute",
            left: shown.x,
            top: shown.y,
            width: 0,
            height: 0,
          }}
        />
      </Menu.Target>
      {/* Portaled out of the canvas, so it opts out of camera keys itself. */}
      <Menu.Dropdown data-captures-keys miw="10em">
        {children(shown)}
      </Menu.Dropdown>
    </Menu>
  );
}

/** Entries of a node's menu: the built-in Edit and Delete, then the custom
 * actions offered for the node's type. */
function NodeMenuEntries({
  node,
  actions,
  onEdit,
  onDelete,
  onAction,
}: {
  node: ViserFlowNode;
  actions: NodeGraphProps["node_actions"];
  onEdit: () => void;
  onDelete: () => void;
  onAction: (actionId: string) => void;
}) {
  const { nodeType } = node.data;
  const custom = actions.filter(
    (a) => a.type_names === null || a.type_names.includes(nodeType.type_name),
  );
  return (
    <>
      <Menu.Label>{nodeType.label}</Menu.Label>
      <Menu.Item leftSection={<IconPencil size="1em" />} onClick={onEdit}>
        Edit
      </Menu.Item>
      <Menu.Item
        color="red"
        leftSection={<IconTrash size="1em" />}
        onClick={onDelete}
      >
        Delete
      </Menu.Item>
      {custom.length > 0 && <Menu.Divider />}
      {custom.map((action) => (
        <Menu.Item
          key={action.action_id}
          leftSection={menuIcon(action.icon_html)}
          onClick={() => onAction(action.action_id)}
        >
          {action.label}
        </Menu.Item>
      ))}
    </>
  );
}

/** Entries of one level of the canvas menu; an entry with children of its own
 * opens a submenu. */
function CanvasMenuEntries({
  parentId,
  childrenOf,
  onClick,
}: {
  parentId: string | null;
  childrenOf: Map<string | null, CanvasMenuItem[]>;
  onClick: (itemId: string) => void;
}) {
  return (childrenOf.get(parentId) ?? []).map((item) => {
    if (item.kind === "divider") return <Menu.Divider key={item.item_id} />;
    const icon = menuIcon(item.icon_html);
    if (!childrenOf.has(item.item_id))
      return (
        <Menu.Item
          key={item.item_id}
          leftSection={icon}
          onClick={() => onClick(item.item_id)}
        >
          {item.label}
        </Menu.Item>
      );
    return (
      <Menu.Sub key={item.item_id}>
        <Menu.Sub.Target>
          <Menu.Sub.Item leftSection={icon}>{item.label}</Menu.Sub.Item>
        </Menu.Sub.Target>
        <Menu.Sub.Dropdown data-captures-keys miw="10em">
          <CanvasMenuEntries
            parentId={item.item_id}
            childrenOf={childrenOf}
            onClick={onClick}
          />
        </Menu.Sub.Dropdown>
      </Menu.Sub>
    );
  });
}

function NodeGraphCanvas({
  uuid,
  props,
}: {
  uuid: string;
  props: NodeGraphProps;
}) {
  const viewer = useContext(ViewerContext)!;
  const colorScheme = useComputedColorScheme("light");
  const [nodes, setNodes, onNodesChangeLocal] = useNodesState<ViserFlowNode>(
    [],
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const paneRef = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();

  // The server owns the graph: re-seed local state whenever it changes.
  React.useEffect(() => {
    setNodes((previous) => toFlowNodes(props, previous));
  }, [props.nodes, props.node_types, props.edges, props.node_status, setNodes]);
  React.useEffect(() => {
    setEdges((previous) => toFlowEdges(props, previous));
  }, [props.edges, setEdges]);

  // Edits are sent to the server, which applies them and syncs every client.
  // Sent unthrottled: the shared throttled sender keeps only the latest
  // message. Read at call time, since sendMessage is reassigned on reconnect.
  const send = (message: Message) =>
    viewer.mutable.current.sendMessage(message);
  const sendNodeEvent = (
    nodeId: string,
    kind: "double_click" | "edit" | "action",
    actionId = "",
  ) =>
    send({
      type: "GuiNodeGraphNodeEventMessage",
      uuid,
      node_id: nodeId,
      kind,
      action_id: actionId,
    });
  const setInputValue = (nodeId: string, port: string, value: InputValue) =>
    send({
      type: "GuiNodeGraphSetValueMessage",
      uuid,
      node_id: nodeId,
      port,
      value,
    });
  const sendDelete = (nodeIds: string[], edgeIds: string[]) =>
    send({
      type: "GuiNodeGraphDeleteMessage",
      uuid,
      node_ids: nodeIds,
      edge_ids: edgeIds,
    });

  const onNodesChange = (changes: NodeChange<ViserFlowNode>[]) => {
    onNodesChangeLocal(changes);
    // Positions are committed when a drag ends or an arrow key moves nodes.
    const moved = changes.flatMap((c) =>
      c.type === "position" && c.dragging !== true && c.position !== undefined
        ? [{ id: c.id, position: c.position }]
        : [],
    );
    if (moved.length > 0)
      send({
        type: "GuiNodeGraphMoveMessage",
        uuid,
        node_ids: moved.map((m) => m.id),
        positions: moved.map((m) => [m.position.x, m.position.y]),
      });
  };

  // The Delete key removes nothing locally; the server's update does.
  const onBeforeDelete: OnBeforeDelete<ViserFlowNode> = async ({
    nodes,
    edges,
  }) => {
    sendDelete(
      nodes.map((n) => n.id),
      edges.map((e) => e.id),
    );
    return false;
  };

  const typeFromNodeId = useMemo(() => {
    const typeFromName = new Map(props.node_types.map((t) => [t.type_name, t]));
    return new Map(
      props.nodes.map((n) => [n.node_id, typeFromName.get(n.type_name)]),
    );
  }, [props.nodes, props.node_types]);

  const isValidConnection: IsValidConnection = (connection) => {
    if (connection.source === connection.target) return false;
    const output = typeFromNodeId
      .get(connection.source)
      ?.outputs.find((p) => p.name === connection.sourceHandle);
    const input = typeFromNodeId
      .get(connection.target)
      ?.inputs.find((p) => p.name === connection.targetHandle);
    return (
      output !== undefined &&
      input !== undefined &&
      dtypesCompatible(output.dtype, input.dtype)
    );
  };

  // The server replaces any wire already plugged into the target input.
  const onConnect = (connection: Connection) =>
    send({
      type: "GuiNodeGraphConnectMessage",
      uuid,
      source: connection.source,
      source_port: connection.sourceHandle ?? "",
      target: connection.target,
      target_port: connection.targetHandle ?? "",
    });

  const childrenOf = useMemo(() => {
    const result = new Map<string | null, CanvasMenuItem[]>();
    for (const item of props.canvas_menu)
      result.set(item.parent_id, [...(result.get(item.parent_id) ?? []), item]);
    return result;
  }, [props.canvas_menu]);

  /** Anchor point of a menu opened by `event`, relative to the canvas. */
  const anchorAt = (event: React.MouseEvent | MouseEvent) => {
    event.preventDefault();
    const pane = paneRef.current?.getBoundingClientRect();
    if (pane === undefined) return null;
    return { x: event.clientX - pane.left, y: event.clientY - pane.top };
  };
  const onNodeContextMenu = (event: React.MouseEvent, node: ViserFlowNode) => {
    const anchor = anchorAt(event);
    if (anchor !== null) setMenu({ ...anchor, kind: "node", node });
  };
  const onPaneContextMenu = (event: React.MouseEvent | MouseEvent) => {
    const anchor = anchorAt(event);
    if (anchor === null || props.canvas_menu.length === 0) return;
    const { x, y } = screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    });
    setMenu({ ...anchor, kind: "canvas", position: [x, y] });
  };

  return (
    <Box ref={paneRef} pos="relative" h="100%">
      <SetInputValueContext.Provider value={setInputValue}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onBeforeDelete={onBeforeDelete}
          isValidConnection={isValidConnection}
          onNodeDoubleClick={(_event, node) =>
            sendNodeEvent(node.id, "double_click")
          }
          onNodeContextMenu={onNodeContextMenu}
          onPaneContextMenu={onPaneContextMenu}
          onMoveStart={() => setMenu(null)}
          // Double-clicks belong to nodes; zooming on them would be surprising.
          zoomOnDoubleClick={false}
          colorMode={colorScheme}
          deleteKeyCode={["Delete", "Backspace"]}
          fitView
          minZoom={0.2}
          proOptions={proOptions}
        >
          <Background />
          <Controls showInteractive={false} />
          {props.show_minimap && (
            <MiniMap
              pannable
              zoomable
              nodeColor={minimapNodeColor}
              style={{ width: 140, height: 90 }}
            />
          )}
        </ReactFlow>
      </SetInputValueContext.Provider>
      <ContextMenu menu={menu} onClose={() => setMenu(null)}>
        {(shown) =>
          shown.kind === "node" ? (
            <NodeMenuEntries
              node={shown.node}
              actions={props.node_actions}
              onEdit={() => sendNodeEvent(shown.node.id, "edit")}
              onDelete={() => sendDelete([shown.node.id], [])}
              onAction={(actionId) =>
                sendNodeEvent(shown.node.id, "action", actionId)
              }
            />
          ) : (
            <CanvasMenuEntries
              parentId={null}
              childrenOf={childrenOf}
              onClick={(itemId) =>
                send({
                  type: "GuiNodeGraphCanvasMenuMessage",
                  uuid,
                  item_id: itemId,
                  position: shown.position,
                })
              }
            />
          )
        }
      </ContextMenu>
    </Box>
  );
}

export default function NodeGraphComponent({
  uuid,
  props,
}: GuiNodeGraphMessage) {
  const rootRef = useRef<HTMLDivElement>(null);
  const { height, marginTop } = useFillHeight(
    rootRef,
    props.height,
    props.visible,
    MIN_FILL_HEIGHT,
  );
  if (!props.visible) return null;
  return (
    <Box
      ref={rootRef}
      // Keeps global shortcuts (camera WASD / arrow keys) out of the canvas,
      // where arrow keys move the selected nodes.
      data-captures-keys
      aria-label={props.label}
      style={{
        height,
        marginTop,
        opacity: props.disabled ? 0.6 : 1,
        pointerEvents: props.disabled ? "none" : undefined,
      }}
    >
      <ReactFlowProvider>
        <NodeGraphCanvas uuid={uuid} props={props} />
      </ReactFlowProvider>
    </Box>
  );
}
