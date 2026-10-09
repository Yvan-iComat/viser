"""Unit tests for the node graph GUI component."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterator
from typing import Any, Literal, cast

import pytest

import viser
from viser import _messages
from viser.infra import ClientId

from .infra_utils import make_synthetic_client


@pytest.fixture
def graph() -> Iterator[viser.GuiNodeGraphHandle]:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        graph = server.gui.add_node_graph()
        graph.add_node_type("load", outputs=[viser.NodePort("mesh", dtype="Mesh")])
        graph.add_node_type(
            "toolpath",
            label="Toolpath",
            inputs=[
                viser.NodePort("mesh", dtype="Mesh"),
                viser.NodePort("stock", dtype="Stock"),
            ],
            outputs=[viser.NodePort("path", dtype="Path")],
        )
        graph.add_node_type("inspect", inputs=[viser.NodePort("value")])
        yield graph
    finally:
        server.stop()


def test_node_types_are_registered(graph: viser.GuiNodeGraphHandle) -> None:
    assert [t.type_name for t in graph.node_types] == ["load", "toolpath", "inspect"]
    load, toolpath, inspect = graph.node_types
    assert load.label == "load"  # Defaults to the type name.
    assert toolpath.label == "Toolpath"
    assert [(p.name, p.label, p.dtype) for p in toolpath.inputs] == [
        ("mesh", "mesh", "Mesh"),
        ("stock", "stock", "Stock"),
    ]
    assert inspect.inputs[0].dtype == "any"

    with pytest.raises(ValueError, match="already registered"):
        graph.add_node_type("load")
    with pytest.raises(ValueError, match="Duplicate input"):
        graph.add_node_type("bad", inputs=[viser.NodePort("a"), viser.NodePort("a")])


def test_add_and_connect_nodes(graph: viser.GuiNodeGraphHandle) -> None:
    load = graph.add_node("load", (10, 20))
    toolpath = graph.add_node("toolpath", node_id="tp")
    assert toolpath == "tp"
    assert [(n.node_id, n.type_name, n.position) for n in graph.nodes] == [
        (load, "load", (10.0, 20.0)),
        ("tp", "toolpath", (0.0, 0.0)),
    ]

    edge_id = graph.connect(load, "mesh", toolpath, "mesh")
    (edge,) = graph.edges
    assert (edge.edge_id, edge.source, edge.source_port) == (edge_id, load, "mesh")
    assert (edge.target, edge.target_port) == ("tp", "mesh")

    with pytest.raises(KeyError, match="no node type"):
        graph.add_node("missing")
    with pytest.raises(ValueError, match="already has a node"):
        graph.add_node("load", node_id="tp")


def test_connect_validates_ports_and_dtypes(graph: viser.GuiNodeGraphHandle) -> None:
    load = graph.add_node("load")
    toolpath = graph.add_node("toolpath")
    inspect = graph.add_node("inspect")

    with pytest.raises(ValueError, match="Cannot wire output"):
        graph.connect(load, "mesh", toolpath, "stock")  # Mesh -> Stock.
    with pytest.raises(KeyError, match="No port"):
        graph.connect(load, "nope", toolpath, "mesh")
    with pytest.raises(KeyError, match="No port"):
        # Inputs can't be wire sources.
        graph.connect(toolpath, "mesh", inspect, "value")
    with pytest.raises(ValueError, match="to itself"):
        graph.connect(toolpath, "path", toolpath, "mesh")
    # An "any" input accepts every dtype.
    graph.connect(toolpath, "path", inspect, "value")
    assert len(graph.edges) == 1


def test_input_accepts_a_single_wire(graph: viser.GuiNodeGraphHandle) -> None:
    first = graph.add_node("load")
    second = graph.add_node("load")
    toolpath = graph.add_node("toolpath")
    graph.connect(first, "mesh", toolpath, "mesh")
    replacement = graph.connect(second, "mesh", toolpath, "mesh")
    assert [(e.edge_id, e.source) for e in graph.edges] == [(replacement, second)]


def test_remove_node_drops_its_wires(graph: viser.GuiNodeGraphHandle) -> None:
    load = graph.add_node("load")
    toolpath = graph.add_node("toolpath")
    inspect = graph.add_node("inspect")
    graph.connect(load, "mesh", toolpath, "mesh")
    kept = graph.connect(toolpath, "path", inspect, "value")

    graph.remove_node(load)
    assert [n.node_id for n in graph.nodes] == [toolpath, inspect]
    assert [e.edge_id for e in graph.edges] == [kept]

    graph.disconnect(kept)
    assert graph.edges == ()
    with pytest.raises(KeyError):
        graph.disconnect(kept)
    with pytest.raises(KeyError):
        graph.remove_node(load)


CLIENT_ID = ClientId(7)


def _dispatch(server: viser.ViserServer, coro: Coroutine[Any, Any, None]) -> None:
    """Run a client-message handler on the server's event loop and block."""
    asyncio.run_coroutine_threadsafe(coro, server._event_loop).result(timeout=5.0)


def _node_event(
    graph: viser.GuiNodeGraphHandle,
    node_id: str,
    kind: Literal["double_click", "edit", "action"],
    action_id: str = "",
) -> None:
    gui_api = graph._impl.gui_api
    message = _messages.GuiNodeGraphNodeEventMessage(
        uuid=graph._impl.uuid, node_id=node_id, kind=kind, action_id=action_id
    )
    _dispatch(
        cast(viser.ViserServer, gui_api._owner),
        gui_api._handle_node_graph_node_event(CLIENT_ID, message),
    )


@pytest.fixture
def server_graph() -> Iterator[viser.GuiNodeGraphHandle]:
    """A graph whose server has a connected (synthetic) client."""
    server = viser.ViserServer(port=0, verbose=False)
    try:
        server._connected_clients[CLIENT_ID] = make_synthetic_client(server, CLIENT_ID)
        graph = server.gui.add_node_graph()
        graph.add_node_type("load", outputs=[viser.NodePort("mesh", dtype="Mesh")])
        graph.add_node_type("slice", inputs=[viser.NodePort("mesh", dtype="Mesh")])
        yield graph
    finally:
        server.stop()


def test_node_events_reach_their_callbacks(
    server_graph: viser.GuiNodeGraphHandle,
) -> None:
    graph = server_graph
    load = graph.add_node("load")
    seen: list[tuple[str, str, str, int | None]] = []

    def recorder(kind: str):
        # Async callbacks run inline on the event loop, so they finish before
        # the dispatch returns.
        async def record(event: viser.NodeGraphNodeEvent) -> None:
            assert event.graph is graph
            seen.append((kind, event.node_id, event.type_name, event.client_id))

        return record

    graph.on_node_double_click(recorder("double_click"))
    graph.on_node_edit(recorder("edit"))
    action = graph.add_node_action("Run", icon=viser.Icon.PLAYER_PLAY)
    action.on_click(recorder("run"))
    (wire_action,) = graph.node_actions
    assert wire_action.label == "Run" and wire_action.icon_html is not None
    assert wire_action.type_names is None

    _node_event(graph, load, "double_click")
    _node_event(graph, load, "edit")
    _node_event(graph, load, "action", wire_action.action_id)
    assert seen == [
        ("double_click", load, "load", CLIENT_ID),
        ("edit", load, "load", CLIENT_ID),
        ("run", load, "load", CLIENT_ID),
    ]

    # Unknown nodes and removed actions are ignored.
    _node_event(graph, "missing", "double_click")
    action.remove()
    assert graph.node_actions == ()
    _node_event(graph, load, "action", wire_action.action_id)
    assert len(seen) == 3


def test_node_action_can_target_node_types(
    graph: viser.GuiNodeGraphHandle,
) -> None:
    graph.add_node_action("Inspect", type_names=["toolpath", "inspect"])
    assert graph.node_actions[0].type_names == ("toolpath", "inspect")


def test_canvas_menu_tree(graph: viser.GuiNodeGraphHandle) -> None:
    add = graph.add_canvas_menu_item("Add node", icon=viser.Icon.PLUS)
    inputs = add.add_item("Inputs")
    inputs.add_item("Load")
    add.add_divider()
    graph.add_canvas_menu_item("Clear")

    menu = graph.canvas_menu
    assert [(i.label, i.kind) for i in menu] == [
        ("Add node", "item"),
        ("Inputs", "item"),
        ("Load", "item"),
        ("", "divider"),
        ("Clear", "item"),
    ]
    assert [i.parent_id for i in menu] == [
        None,
        menu[0].item_id,
        menu[1].item_id,
        menu[0].item_id,
        None,
    ]
    assert menu[0].icon_html is not None and menu[1].icon_html is None

    # Removing an entry removes its whole submenu.
    add.remove()
    assert [i.label for i in graph.canvas_menu] == ["Clear"]
    with pytest.raises(ValueError, match="removed"):
        inputs.add_item("Orphan")


def test_canvas_menu_rejects_bad_parents(graph: viser.GuiNodeGraphHandle) -> None:
    divider = graph.add_canvas_menu_divider()
    with pytest.raises(ValueError, match="divider"):
        divider.add_item("Nope")
    other = graph._impl.gui_api.add_node_graph()
    with pytest.raises(ValueError, match="another node graph"):
        other.add_canvas_menu_item("Nope", parent=graph.add_canvas_menu_item("Mine"))


def test_canvas_menu_clicks_reach_their_callbacks(
    server_graph: viser.GuiNodeGraphHandle,
) -> None:
    graph = server_graph
    gui_api = graph._impl.gui_api
    server = cast(viser.ViserServer, gui_api._owner)
    add = graph.add_canvas_menu_item("Add node")
    load_entry = add.add_item("Load")

    @load_entry.on_click
    async def _(event: viser.NodeGraphCanvasEvent) -> None:
        assert event.graph is graph and event.client_id == CLIENT_ID
        event.graph.add_node("load", event.position)

    def click(item: viser.NodeGraphMenuItemHandle) -> None:
        _dispatch(
            server,
            gui_api._handle_node_graph_canvas_menu(
                CLIENT_ID,
                # Positions arrive from msgpack as lists.
                _messages.GuiNodeGraphCanvasMenuMessage(
                    graph._impl.uuid, item._item_id, cast(Any, [12.5, -40])
                ),
            ),
        )

    click(load_entry)
    assert [(n.type_name, n.position) for n in graph.nodes] == [("load", (12.5, -40.0))]
    # Removed entries no longer fire.
    load_entry.remove()
    click(load_entry)
    assert len(graph.nodes) == 1


def test_client_edits_update_the_graph(
    server_graph: viser.GuiNodeGraphHandle,
) -> None:
    graph = server_graph
    gui_api = graph._impl.gui_api
    server = cast(viser.ViserServer, gui_api._owner)
    uuid = graph._impl.uuid
    load = graph.add_node("load")
    first = graph.add_node("slice")
    second = graph.add_node("slice")

    _dispatch(
        server,
        gui_api._handle_node_graph_connect(
            CLIENT_ID,
            _messages.GuiNodeGraphConnectMessage(uuid, load, "mesh", first, "mesh"),
        ),
    )
    (edge,) = graph.edges
    assert (edge.source, edge.target) == (load, first)

    # Invalid wires from a client warn instead of raising in the handler.
    with pytest.warns(UserWarning, match="Ignoring invalid wire"):
        _dispatch(
            server,
            gui_api._handle_node_graph_connect(
                CLIENT_ID,
                _messages.GuiNodeGraphConnectMessage(uuid, first, "mesh", load, "mesh"),
            ),
        )

    _dispatch(
        server,
        gui_api._handle_node_graph_move(
            CLIENT_ID,
            # Positions arrive from msgpack as lists.
            _messages.GuiNodeGraphMoveMessage(
                uuid, (first, "missing"), cast(Any, ([10, 20], [0, 0]))
            ),
        ),
    )
    assert [n.position for n in graph.nodes] == [(0.0, 0.0), (10.0, 20.0), (0.0, 0.0)]

    _dispatch(
        server,
        gui_api._handle_node_graph_delete(
            CLIENT_ID,
            _messages.GuiNodeGraphDeleteMessage(uuid, (first, "missing"), ()),
        ),
    )
    assert [n.node_id for n in graph.nodes] == [load, second]
    assert graph.edges == ()  # The wire into the deleted node went with it.


def _summary(event: viser.NodeGraphChangeEvent) -> dict[str, tuple[str, ...]]:
    """The non-empty fields of a change event."""
    fields = ("added_nodes", "removed_nodes", "moved_nodes")
    fields += ("added_edges", "removed_edges")
    return {f: getattr(event, f) for f in fields if getattr(event, f)}


def test_python_edits_are_reported(graph: viser.GuiNodeGraphHandle) -> None:
    changes: list[viser.NodeGraphChangeEvent] = []
    removed: list[tuple[str, str]] = []

    # Sync callbacks run inline for edits made from Python.
    @graph.on_change
    def _(event: viser.NodeGraphChangeEvent) -> None:
        assert event.graph is graph and event.client is None
        changes.append(event)

    @graph.on_node_remove
    def _(event: viser.NodeGraphNodeEvent) -> None:
        assert event.client is None
        assert event.node_id not in [n.node_id for n in graph.nodes]
        removed.append((event.node_id, event.type_name))

    first = graph.add_node("load")
    second = graph.add_node("load")
    toolpath = graph.add_node("toolpath")
    wire = graph.connect(first, "mesh", toolpath, "mesh")
    replacement = graph.connect(second, "mesh", toolpath, "mesh")
    with pytest.raises(ValueError):
        graph.connect(first, "mesh", toolpath, "stock")  # Fails: not reported.
    graph.remove_node(second)
    assert [_summary(c) for c in changes] == [
        {"added_nodes": (first,)},
        {"added_nodes": (second,)},
        {"added_nodes": (toolpath,)},
        {"added_edges": (wire,)},
        {"added_edges": (replacement,), "removed_edges": (wire,)},
        {"removed_nodes": (second,), "removed_edges": (replacement,)},
    ]
    assert removed == [(second, "load")]


def test_client_edits_are_reported(server_graph: viser.GuiNodeGraphHandle) -> None:
    graph = server_graph
    gui_api = graph._impl.gui_api
    server = cast(viser.ViserServer, gui_api._owner)
    uuid = graph._impl.uuid
    load = graph.add_node("load")
    slice_ = graph.add_node("slice")
    changes: list[viser.NodeGraphChangeEvent] = []
    removed: list[tuple[str, str, int | None]] = []

    # Async callbacks run inline on the event loop for edits from the browser.
    @graph.on_change
    async def _(event: viser.NodeGraphChangeEvent) -> None:
        assert event.client is not None and event.client_id == CLIENT_ID
        changes.append(event)

    @graph.on_node_remove
    async def _(event: viser.NodeGraphNodeEvent) -> None:
        removed.append((event.node_id, event.type_name, event.client_id))

    def send(handler: Any, message: Any) -> None:
        _dispatch(server, handler(CLIENT_ID, message))

    send(
        gui_api._handle_node_graph_connect,
        _messages.GuiNodeGraphConnectMessage(uuid, load, "mesh", slice_, "mesh"),
    )
    (wire,) = graph.edges
    move = _messages.GuiNodeGraphMoveMessage(
        uuid, (slice_, "missing"), cast(Any, ([10, 20], [0, 0]))
    )
    send(gui_api._handle_node_graph_move, move)
    # Moving to the same place, or deleting what is gone, changes nothing.
    send(gui_api._handle_node_graph_move, move)
    send(
        gui_api._handle_node_graph_delete,
        _messages.GuiNodeGraphDeleteMessage(uuid, ("missing",), ("missing",)),
    )
    send(
        gui_api._handle_node_graph_delete,
        _messages.GuiNodeGraphDeleteMessage(uuid, (slice_,), ()),
    )
    assert [_summary(c) for c in changes] == [
        {"added_edges": (wire.edge_id,)},
        {"moved_nodes": (slice_,)},
        {"removed_nodes": (slice_,), "removed_edges": (wire.edge_id,)},
    ]
    assert removed == [(slice_, "slice", CLIENT_ID)]


def test_add_node_graph_rejects_bad_arguments() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        with pytest.raises(ValueError, match="height"):
            server.gui.add_node_graph(height=0)
        with pytest.raises(TypeError, match="add_tab"):
            # Deliberately the wrong container type.
            server.gui.add_node_graph(parent=cast(Any, server.gui.add_panel()))
    finally:
        server.stop()
