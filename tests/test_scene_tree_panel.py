"""Unit tests for the scene tree panel (`server.gui.add_scene_tree`)."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

import viser
from viser import _messages
from viser.infra import ClientId


@pytest.fixture
def server():
    server = viser.ViserServer(port=0, verbose=False)
    try:
        yield server
    finally:
        server.stop()


def test_placement_and_props(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree("Project", width=300, background_opacity=0.25)
    assert tree.placement == "window"
    assert tree.label == "Project"
    assert tree.width == 300
    assert tree.background_opacity == 0.25
    assert tree.items == ()
    assert tree.show_scene_nodes

    with server.gui.add_folder("Folder"):
        inline = server.gui.add_scene_tree(parent=None)
    assert inline.placement == "inline"


def test_invalid_arguments_rejected(server: viser.ViserServer) -> None:
    with pytest.raises(ValueError):
        server.gui.add_scene_tree(width=0)
    with pytest.raises(ValueError):
        server.gui.add_scene_tree(background_opacity=1.5)
    with pytest.raises(TypeError):
        server.gui.add_scene_tree(parent=server.gui.add_panel())


def test_add_edit_remove_items(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree()
    parts = tree.add_item("Parts", icon=viser.Icon.BOX, item_id="parts")
    bracket = parts.add_item("Bracket", item_id="bracket")
    bolt = bracket.add_item("Bolt")
    other = tree.add_item("Other")

    assert [h.item_id for h in tree.root_items] == ["parts", other.item_id]
    assert parts.children == (bracket,)
    assert bracket.parent is parts
    assert tree.get_item("bracket") is bracket
    assert parts.icon == viser.Icon.BOX
    assert tree.items[0]._icon_html is not None

    # Edits rewrite only the targeted entry.
    bracket.name = "Bracket v2"
    bracket.visible = False
    parts.icon = None
    assert tree.get_item("bracket").name == "Bracket v2"  # type: ignore[union-attr]
    assert not bracket.visible
    assert tree.items[0]._icon_html is None
    assert other.visible

    # Removing a parent removes its subtree.
    parts.remove()
    assert parts.removed and bracket.removed and bolt.removed
    assert not other.removed
    assert [item.item_id for item in tree.items] == [other.item_id]
    # Removed handles keep reporting their last state, and ignore writes.
    assert bracket.name == "Bracket v2"
    bracket.name = "ghost"
    assert [item.name for item in tree.items] == ["Other"]

    tree.clear_items()
    assert tree.items == ()


def test_duplicate_and_unknown_ids_rejected(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree()
    tree.add_item("A", item_id="a")
    with pytest.raises(ValueError):
        tree.add_item("A again", item_id="a")
    with pytest.raises(ValueError):
        tree.add_item("Orphan", parent="missing")
    with pytest.raises(ValueError):
        tree.select("missing")
    # Removing an unknown id is a no-op.
    tree.remove_item("missing")


def test_on_change_fires_for_python_edits(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree()
    events: list[tuple[str, str, Any]] = []

    @tree.on_change
    def _(event: viser.SceneTreeItemEvent) -> None:
        events.append((event.action, event.item_id, event.client))

    a = tree.add_item("A", item_id="a")
    a.add_item("B", item_id="b")
    a.name = "A"  # Unchanged: no event.
    a.name = "A2"
    a.remove()

    assert events == [
        ("add", "a", None),
        ("add", "b", None),
        ("edit", "a", None),
        ("remove", "a", None),
    ]


def test_selection(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree()
    a = tree.add_item("A")
    assert tree.selected is None
    tree.select(a)
    assert tree.selected is a
    a.remove()
    assert tree.selected_id is None


def _dispatch(
    server: viser.ViserServer,
    tree: viser.GuiSceneTreeHandle,
    item_id: str,
    action: Any,
    value: str = "",
) -> None:
    """Simulate a browser action message, as the websocket handler would."""
    client = object()  # Only identity matters to the handle.
    gui_api = server.gui
    gui_api._resolve_client = lambda client_id: client  # type: ignore[method-assign]
    message = _messages.GuiSceneTreeActionMessage(
        uuid=tree._impl.uuid, item_id=item_id, action=action, value=value
    )
    asyncio.run(gui_api._handle_scene_tree_action(ClientId(0), message))


def test_browser_actions_apply_and_fire_callbacks(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree()
    a = tree.add_item("A", item_id="a", editable=True, removable=True)
    child = a.add_item("Child", item_id="child")
    locked = tree.add_item("Locked", item_id="locked")

    fired: list[tuple[str, str]] = []
    done = threading.Semaphore(0)

    def record(event: viser.SceneTreeItemEvent) -> None:
        fired.append((event.action, event.item_id))
        done.release()

    a.on_click(record)
    tree.on_visibility_change(record)
    tree.on_rename(record)
    tree.on_remove(record)

    def wait(n: int) -> None:
        for _ in range(n):
            assert done.acquire(timeout=5.0)

    _dispatch(server, tree, "a", "click")
    wait(1)
    assert tree.selected is a

    _dispatch(server, tree, "a", "visibility", "false")
    wait(1)
    assert not a.visible

    _dispatch(server, tree, "a", "rename", "  Renamed  ")
    wait(1)
    assert a.name == "Renamed"

    # Non-editable / non-removable items ignore browser edits.
    _dispatch(server, tree, "locked", "rename", "Nope")
    _dispatch(server, tree, "locked", "remove")
    assert locked.name == "Locked" and not locked.removed

    _dispatch(server, tree, "a", "remove")
    wait(1)
    assert a.removed and child.removed
    assert tree.selected_id is None

    assert fired == [
        ("click", "a"),
        ("visibility", "a"),
        ("rename", "a"),
        ("remove", "a"),
    ]


def test_menu_entries_scope_and_cascade(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree()
    parts = tree.add_item("Parts", item_id="parts")
    other = tree.add_item("Other", item_id="other")

    everywhere = tree.add_menu_item("Info", menu_id="info")
    add = parts.add_menu_item("Add", icon=viser.Icon.PLUS, menu_id="add")
    box = add.add_menu_item("Box", menu_id="add-box")

    assert everywhere.item is None
    assert add.item is parts
    # Submenu entries inherit their root's item scope.
    assert box.item is parts
    assert add.children == (box,)
    assert tree.get_menu_item("add-box") is box
    assert tree.menu[1]._icon_html is not None

    with pytest.raises(ValueError):
        other.add_menu_item("Dup", menu_id="add")

    box.label = "Cube"
    assert tree.menu[2].label == "Cube"

    # Removing an entry removes its submenu.
    add.remove()
    assert add.removed and box.removed
    assert [entry.menu_id for entry in tree.menu] == ["info"]

    # Removing an item removes the entries scoped to it.
    sub = parts.add_menu_item("Add again").add_menu_item("Leaf")
    parts.remove()
    assert sub.removed
    assert [entry.menu_id for entry in tree.menu] == ["info"]


def test_menu_click_appends_child(server: viser.ViserServer) -> None:
    tree = server.gui.add_scene_tree()
    parts = tree.add_item("Parts", item_id="parts")
    other = tree.add_item("Other", item_id="other")
    add_box = parts.add_menu_item("Add").add_menu_item("Box", menu_id="add-box")
    info = tree.add_menu_item("Info", menu_id="info")

    fired: list[tuple[str, str, str | None]] = []
    done = threading.Semaphore(0)

    @add_box.on_click
    def _(event: viser.SceneTreeItemEvent) -> None:
        event.target.add_item("Box", item_id="box")
        done.release()

    @tree.on_menu_click
    def _(event: viser.SceneTreeItemEvent) -> None:
        assert event.menu_item is not None
        fired.append((event.action, event.item_id, event.menu_item.menu_id))
        done.release()

    _dispatch(server, tree, "parts", "menu", "add-box")
    for _i in range(2):
        assert done.acquire(timeout=5.0)
    assert [child.item_id for child in parts.children] == ["box"]

    # An entry scoped to another item is ignored; tree-wide entries work anywhere.
    _dispatch(server, tree, "other", "menu", "add-box")
    _dispatch(server, tree, "other", "menu", "info")
    assert done.acquire(timeout=5.0)
    assert other.children == ()
    assert info.item is None
    assert fired == [("menu", "parts", "add-box"), ("menu", "other", "info")]
