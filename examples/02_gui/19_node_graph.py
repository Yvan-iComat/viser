"""Node graph

Add a node graph editor in a floating panel: typed nodes whose output ports are
wired to the input ports of other nodes. Wires can only join ports of the same
data type (or ``"any"``). Inputs declared with a default value -- the tool
diameter of the Toolpath node, say -- can be edited right in the node while no
wire is plugged into them.

Double-click a node, or pick Edit in its right-click menu, to open its settings
in a new floating panel. The menu also offers custom actions: Duplicate for
every node, and Run simulation for the Simulate node. Right-click the empty
canvas to add new blocks from the "Add node" submenu, or to clear the graph.

Every edit is reported to Python and logged to the console. A node's settings
panel follows its inputs: editing a value or rewiring updates the panel, and
the panel's fields edit the node. Removing a node closes its panel.

Nodes show the progress of the work their callbacks start: changing an input of
a Toolpath node recomputes it, and Run simulation simulates. A spinner turns in
the node's header meanwhile, then a mark shows success or the error found --
hover it for details. Try a step-over above 1, or unplugging the mesh.

* :meth:`viser.GuiApi.add_node_graph` to create the graph inside a panel tab
* :meth:`viser.GuiNodeGraphHandle.add_node_type` to register kinds of node,
  with editable inputs given by :attr:`viser.NodePort.default`
* :meth:`viser.GuiNodeGraphHandle.add_node` and
  :meth:`viser.GuiNodeGraphHandle.connect` to build the graph
* :meth:`viser.GuiNodeGraphHandle.on_node_double_click` and
  :meth:`viser.GuiNodeGraphHandle.on_node_edit` to react to the user
* :meth:`viser.GuiNodeGraphHandle.add_node_action` for custom node menu entries
* :meth:`viser.GuiNodeGraphHandle.add_canvas_menu_item` for the canvas menu,
  with nested submenus
* :meth:`viser.GuiNodeGraphHandle.on_node_add`,
  :meth:`viser.GuiNodeGraphHandle.on_node_input_change`,
  :meth:`viser.GuiNodeGraphHandle.on_node_remove` and
  :meth:`viser.GuiNodeGraphHandle.on_change` to follow edits of the graph
* :meth:`viser.GuiNodeGraphHandle.node_running` to show a node's progress
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Union

import viser

InputHandle = Union[viser.GuiCheckboxHandle, viser.GuiNumberHandle, viser.GuiTextHandle]


def main() -> None:
    server = viser.ViserServer()

    panel = server.gui.add_panel()
    graph = server.gui.add_node_graph(
        "CAM pipeline", parent=panel.add_tab("Flow", viser.Icon.SITEMAP)
    )
    panel.float(x=-15, y=15, width=960, height=560)

    graph.add_node_type(
        "load_stl",
        label="Load STL",
        color="#1c7ed6",
        inputs=[viser.NodePort("file", dtype="str", default="bracket.stl")],
        outputs=[viser.NodePort("mesh", dtype="Mesh")],
    )
    graph.add_node_type(
        "material",
        label="Material",
        color="#2f9e44",
        inputs=[viser.NodePort("grade", dtype="str", default="Al 6061")],
        outputs=[viser.NodePort("stock", dtype="Stock")],
    )
    graph.add_node_type(
        "number",
        label="Number",
        color="#868e96",
        inputs=[viser.NodePort("value", dtype="float", default=10.0)],
        outputs=[viser.NodePort("value", dtype="float")],
    )
    graph.add_node_type(
        "toolpath",
        label="Toolpath",
        color="#e8590c",
        inputs=[
            viser.NodePort("mesh", dtype="Mesh"),
            viser.NodePort("stock", dtype="Stock"),
            viser.NodePort("tool_d", dtype="float", label="tool Ø", default=6.0),
            viser.NodePort("step_over", dtype="float", label="step-over", default=0.4),
        ],
        outputs=[viser.NodePort("path", dtype="Path")],
    )
    graph.add_node_type(
        "simulate",
        label="Simulate",
        color="#7048e8",
        inputs=[
            viser.NodePort("path", dtype="Path"),
            viser.NodePort("stock", dtype="Stock"),
            viser.NodePort("samples", dtype="int", default=100),
            viser.NodePort("collisions", dtype="bool", default=True),
        ],
    )
    graph.add_node_type(
        "inspect",
        label="Inspect",
        inputs=[viser.NodePort("value", dtype="any")],
    )
    type_from_name = {t.type_name: t for t in graph.node_types}

    # Right-clicking the empty canvas opens this menu. Its "Add node" submenu
    # places a new block where the user right-clicked.
    def add_node_entry(parent: viser.NodeGraphMenuItemHandle, type_name: str) -> None:
        entry = parent.add_item(type_from_name[type_name].label)

        @entry.on_click
        def _(event: viser.NodeGraphCanvasEvent) -> None:
            graph.add_node(type_name, event.position)

    add_menu = graph.add_canvas_menu_item("Add node", icon=viser.Icon.PLUS)
    inputs_menu = add_menu.add_item("Inputs", icon=viser.Icon.FILE_IMPORT)
    add_node_entry(inputs_menu, "load_stl")
    add_node_entry(inputs_menu, "material")
    add_node_entry(inputs_menu, "number")
    processing_menu = add_menu.add_item("Processing", icon=viser.Icon.TOOL)
    add_node_entry(processing_menu, "toolpath")
    add_node_entry(processing_menu, "simulate")
    add_menu.add_divider()
    add_node_entry(add_menu, "inspect")

    graph.add_canvas_menu_divider()
    clear = graph.add_canvas_menu_item("Clear graph", icon=viser.Icon.TRASH)

    @clear.on_click
    def _(_: viser.NodeGraphCanvasEvent) -> None:
        for node in graph.nodes:
            graph.remove_node(node.node_id)

    load = graph.add_node("load_stl", (0, 0))
    material = graph.add_node("material", (0, 130))
    graph.add_node("number", (0, 260))
    toolpath = graph.add_node("toolpath", (300, 40))
    simulate = graph.add_node("simulate", (620, 40))
    graph.add_node("inspect", (620, 230))

    graph.connect(load, "mesh", toolpath, "mesh")
    graph.connect(material, "stock", toolpath, "stock")
    graph.connect(toolpath, "path", simulate, "path")

    def node_name(node_id: str) -> str:
        node = next(n for n in graph.nodes if n.node_id == node_id)
        return f"{type_from_name[node.type_name].label} {node_id[:6]}"

    def describe_inputs(node_id: str) -> str:
        """One line per input: the node wired into it, else its own value."""
        node = next(n for n in graph.nodes if n.node_id == node_id)
        source_from_port = {
            e.target_port: e.source for e in graph.edges if e.target == node_id
        }
        lines = []
        for port in type_from_name[node.type_name].inputs:
            if port.name in source_from_port:
                state = f"wired from {node_name(source_from_port[port.name])}"
            elif port.name in node.values:
                state = f"{node.values[port.name]!r}"
            else:
                state = "*not wired*"
            lines.append(f"- `{port.label}`: {state}")
        return "\n".join(lines) if lines else "No inputs."

    # One settings panel per node, reopened on top when asked again. It keeps
    # its summary and fields to follow the node's inputs.
    class NodePanel:
        def __init__(self, node_id: str, type_name: str) -> None:
            self.node_id = node_id
            self.panel = server.gui.add_panel()
            self.fields: dict[str, InputHandle] = {}
            with self.panel.add_tab(
                type_from_name[type_name].label, viser.Icon.SETTINGS
            ):
                self.summary = server.gui.add_markdown(describe_inputs(node_id))
                for port in type_from_name[type_name].inputs:
                    if port.value_kind is not None:
                        self.add_field(port.name, port.label)
                close = server.gui.add_button("Close", icon=viser.Icon.X)

            @close.on_click
            def _(_: viser.GuiEvent) -> None:
                close_node_panel(node_id)

        def add_field(self, port: str, label: str) -> None:
            value = graph.get_input_value(self.node_id, port)
            field: InputHandle
            if isinstance(value, bool):
                field = server.gui.add_checkbox(label, initial_value=value)
            elif isinstance(value, (int, float)):
                field = server.gui.add_number(label, initial_value=value)
            else:
                field = server.gui.add_text(label, initial_value=value)
            # A wired input takes its value from the wire.
            field.disabled = self.is_wired(port)
            self.fields[port] = field

            # Panel -> node. Setting the same value again changes nothing, so
            # the node -> panel update below doesn't echo back.
            @field.on_update
            def _(_: viser.GuiEvent) -> None:
                graph.set_input_value(self.node_id, port, field.value)

        def is_wired(self, port: str) -> bool:
            return any(
                e.target == self.node_id and e.target_port == port for e in graph.edges
            )

        def refresh(self, event: viser.NodeGraphInputEvent) -> None:
            self.summary.content = describe_inputs(self.node_id)
            field = self.fields.get(event.port, None)
            if (
                event.reason == "value"
                and field is not None
                and event.value is not None
            ):
                field.value = event.value  # type: ignore[assignment]
            if field is not None:
                field.disabled = self.is_wired(event.port)

    panels: dict[str, NodePanel] = {}

    def close_node_panel(node_id: str) -> None:
        if node_id in panels:
            panels.pop(node_id).panel.remove()

    def open_node_panel(event: viser.NodeGraphNodeEvent) -> None:
        close_node_panel(event.node_id)
        node_panel = NodePanel(event.node_id, event.type_name)
        panels[event.node_id] = node_panel
        offset = 30 * (len(panels) - 1)
        node_panel.panel.float(x=20 + offset, y=60 + offset, width=320, height=300)

    graph.on_node_double_click(open_node_panel)
    graph.on_node_edit(open_node_panel)

    # Per-node events: a node was added, one of its inputs changed (its value
    # was edited, or a wire was plugged or unplugged), or it was removed.
    @graph.on_node_add
    def _(event: viser.NodeGraphNodeEvent) -> None:
        print(f"Added {node_name(event.node_id)}")

    @graph.on_node_input_change
    def _(event: viser.NodeGraphInputEvent) -> None:
        if event.reason == "value":
            what = f"set to {event.value!r}"
        else:
            assert event.edge is not None
            what = f"{event.reason} ({event.edge.source[:6]}.{event.edge.source_port})"
        print(f"{node_name(event.node_id)}: input {event.port!r} {what}")
        if event.node_id in panels:
            panels[event.node_id].refresh(event)
        if event.type_name == "toolpath":
            # Long work goes to a worker thread, so the callback returns at
            # once; the node shows a spinner until the work is done.
            workers.submit(compute_toolpath, event.node_id)

    workers = ThreadPoolExecutor(max_workers=4)

    def wired_ports(node_id: str) -> set[str]:
        return {e.target_port for e in graph.edges if e.target == node_id}

    def compute_toolpath(node_id: str) -> None:
        with graph.node_running(node_id) as run:
            time.sleep(1.5)  # Stands for the real computation.
            missing = {"mesh", "stock"} - wired_ports(node_id)
            step_over = graph.get_input_value(node_id, "step_over")
            tool_d = graph.get_input_value(node_id, "tool_d")
            assert isinstance(step_over, float) and isinstance(tool_d, float)
            if missing:
                run.error(f"Wire the {' and '.join(sorted(missing))} input first.")
            elif not 0.0 < step_over <= 1.0:
                run.error(f"Step-over must be in (0, 1], got {step_over}.")
            else:
                passes = max(1, round(1.0 / step_over))
                run.success(f"{passes} passes with a {tool_d} mm tool.")

    @graph.on_node_remove
    def _(event: viser.NodeGraphNodeEvent) -> None:
        print(f"Removed {event.type_name} {event.node_id[:6]}")
        close_node_panel(event.node_id)

    # The whole edit at once, e.g. to mark results out of date. Here, count
    # the revisions of the graph that structure or inputs went through.
    revision = 0

    @graph.on_change
    def _(event: viser.NodeGraphChangeEvent) -> None:
        nonlocal revision
        # Moving nodes changes no result.
        if (
            event.added_nodes
            or event.removed_nodes
            or event.added_edges
            or event.removed_edges
            or event.changed_values
        ):
            revision += 1

    duplicate = graph.add_node_action("Duplicate", icon=viser.Icon.COPY)

    @duplicate.on_click
    def _(event: viser.NodeGraphNodeEvent) -> None:
        node = next(n for n in graph.nodes if n.node_id == event.node_id)
        graph.add_node(
            node.type_name,
            (node.position[0] + 40, node.position[1] + 40),
            values=node.values,
        )

    run = graph.add_node_action(
        "Run simulation", icon=viser.Icon.PLAYER_PLAY, type_names=["simulate"]
    )

    @run.on_click
    def _(event: viser.NodeGraphNodeEvent) -> None:
        workers.submit(run_simulation, event.node_id)

    def run_simulation(node_id: str) -> None:
        with graph.node_running(node_id) as run:
            samples = graph.get_input_value(node_id, "samples")
            assert isinstance(samples, int)
            time.sleep(min(5.0, 0.02 * samples))  # Stands for the simulation.
            if "path" not in wired_ports(node_id):
                run.error("No toolpath to simulate: wire the path input.")
            elif samples <= 0:
                run.error("Samples must be positive.")
            else:
                run.success(
                    f"{samples} samples on graph revision {revision}, no collision."
                )

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    main()
