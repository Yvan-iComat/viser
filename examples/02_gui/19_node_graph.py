"""Node graph

Add a node graph editor in a floating panel: typed nodes whose output ports are
wired to the input ports of other nodes. Wires can only join ports of the same
data type (or ``"any"``).

Double-click a node, or pick Edit in its right-click menu, to open its settings
in a new floating panel. The menu also offers custom actions: Duplicate for
every node, and Run simulation for the Simulate node. Right-click the empty
canvas to add new blocks from the "Add node" submenu, or to clear the graph.
Every edit is reported to Python: removing a node closes its settings panel,
and rewiring refreshes the wired inputs shown in open panels.

* :meth:`viser.GuiApi.add_node_graph` to create the graph inside a panel tab
* :meth:`viser.GuiNodeGraphHandle.add_node_type` to register kinds of node
* :meth:`viser.GuiNodeGraphHandle.add_node` and
  :meth:`viser.GuiNodeGraphHandle.connect` to build the graph
* :meth:`viser.GuiNodeGraphHandle.on_node_double_click` and
  :meth:`viser.GuiNodeGraphHandle.on_node_edit` to react to the user
* :meth:`viser.GuiNodeGraphHandle.add_node_action` for custom node menu entries
* :meth:`viser.GuiNodeGraphHandle.add_canvas_menu_item` for the canvas menu,
  with nested submenus
* :meth:`viser.GuiNodeGraphHandle.on_node_remove` and
  :meth:`viser.GuiNodeGraphHandle.on_change` to follow edits of the graph
"""

from __future__ import annotations

import time

import viser


def main() -> None:
    server = viser.ViserServer()

    panel = server.gui.add_panel()
    graph = server.gui.add_node_graph(
        "CAM pipeline", parent=panel.add_tab("Flow", viser.Icon.SITEMAP)
    )
    panel.float(x=-15, y=15, width=900, height=520)

    graph.add_node_type(
        "load_stl",
        label="Load STL",
        color="#1c7ed6",
        outputs=[viser.NodePort("mesh", dtype="Mesh")],
    )
    graph.add_node_type(
        "material",
        label="Material",
        color="#2f9e44",
        outputs=[viser.NodePort("stock", dtype="Stock")],
    )
    graph.add_node_type(
        "toolpath",
        label="Toolpath",
        color="#e8590c",
        inputs=[
            viser.NodePort("mesh", dtype="Mesh"),
            viser.NodePort("stock", dtype="Stock"),
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
        ],
    )
    graph.add_node_type(
        "inspect",
        label="Inspect",
        inputs=[viser.NodePort("value", dtype="any")],
    )

    # Right-clicking the empty canvas opens this menu. Its "Add node" submenu
    # places a new block where the user right-clicked.
    def add_node_entry(parent: viser.NodeGraphMenuItemHandle, type_name: str) -> None:
        label = next(t.label for t in graph.node_types if t.type_name == type_name)
        entry = parent.add_item(label)

        @entry.on_click
        def _(event: viser.NodeGraphCanvasEvent) -> None:
            graph.add_node(type_name, event.position)

    add_menu = graph.add_canvas_menu_item("Add node", icon=viser.Icon.PLUS)
    inputs_menu = add_menu.add_item("Inputs", icon=viser.Icon.FILE_IMPORT)
    add_node_entry(inputs_menu, "load_stl")
    add_node_entry(inputs_menu, "material")
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
    material = graph.add_node("material", (0, 140))
    toolpath = graph.add_node("toolpath", (260, 40))
    simulate = graph.add_node("simulate", (520, 60))
    graph.add_node("inspect", (520, 200))

    graph.connect(load, "mesh", toolpath, "mesh")
    graph.connect(material, "stock", toolpath, "stock")
    graph.connect(toolpath, "path", simulate, "path")

    label_from_type = {t.type_name: t.label for t in graph.node_types}

    def describe_node(node_id: str) -> str:
        label_from_node = {n.node_id: label_from_type[n.type_name] for n in graph.nodes}
        wired_inputs = [
            f"`{e.target_port}` from {label_from_node[e.source]}"
            for e in graph.edges
            if e.target == node_id
        ]
        return f"**Node** `{node_id[:8]}`\n\n" + (
            "**Wired inputs:** " + ", ".join(wired_inputs)
            if wired_inputs
            else "No wired inputs."
        )

    # One settings panel per node, reopened on top when asked again. The
    # markdown is kept to refresh the wired inputs when the wires change.
    panels: dict[str, tuple[viser.PanelHandle, viser.GuiMarkdownHandle]] = {}

    def close_node_panel(node_id: str) -> None:
        if node_id in panels:
            panels.pop(node_id)[0].remove()

    def open_node_panel(event: viser.NodeGraphNodeEvent) -> None:
        close_node_panel(event.node_id)
        panel = server.gui.add_panel()
        with panel.add_tab(label_from_type[event.type_name], viser.Icon.SETTINGS):
            summary = server.gui.add_markdown(describe_node(event.node_id))
            server.gui.add_slider(
                "Feed rate", min=0, max=5000, step=50, initial_value=1200
            )
            server.gui.add_checkbox("Enabled", initial_value=True)
            close = server.gui.add_button("Close", icon=viser.Icon.X)
        panels[event.node_id] = (panel, summary)

        @close.on_click
        def _(_: viser.GuiEvent) -> None:
            if panels.get(event.node_id, (None,))[0] is panel:
                del panels[event.node_id]
            panel.remove()

        offset = 30 * (len(panels) - 1)
        panel.float(x=20 + offset, y=60 + offset, width=320, height=260)

    graph.on_node_double_click(open_node_panel)
    graph.on_node_edit(open_node_panel)

    # A removed node's settings panel goes with it, whether the node was
    # deleted in the browser or by "Clear graph".
    @graph.on_node_remove
    def _(event: viser.NodeGraphNodeEvent) -> None:
        close_node_panel(event.node_id)

    # Every edit is reported once: here, logged and used to keep the wired
    # inputs shown in open settings panels up to date.
    @graph.on_change
    def _(event: viser.NodeGraphChangeEvent) -> None:
        who = "Python" if event.client_id is None else f"client {event.client_id}"
        counts = {
            "added nodes": len(event.added_nodes),
            "removed nodes": len(event.removed_nodes),
            "moved nodes": len(event.moved_nodes),
            "added wires": len(event.added_edges),
            "removed wires": len(event.removed_edges),
        }
        print(
            f"Graph edited by {who}:",
            ", ".join(f"{n} {what}" for what, n in counts.items() if n),
        )
        if event.added_edges or event.removed_edges:
            for node_id, (_, summary) in list(panels.items()):
                summary.content = describe_node(node_id)

    duplicate = graph.add_node_action("Duplicate", icon=viser.Icon.COPY)

    @duplicate.on_click
    def _(event: viser.NodeGraphNodeEvent) -> None:
        node = next(n for n in graph.nodes if n.node_id == event.node_id)
        graph.add_node(node.type_name, (node.position[0] + 40, node.position[1] + 40))

    run = graph.add_node_action(
        "Run simulation", icon=viser.Icon.PLAYER_PLAY, type_names=["simulate"]
    )

    @run.on_click
    def _(event: viser.NodeGraphNodeEvent) -> None:
        if event.client is not None:
            event.client.add_notification(
                "Simulation",
                f"Running simulation node {event.node_id[:8]}...",
                auto_close_seconds=3.0,
            )

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    main()
