"""Scene tree panel

A floating, see-through panel listing the scene graph plus custom items.

* :meth:`viser.GuiApi.add_scene_tree` adds the panel and returns a
  :class:`viser.GuiSceneTreeHandle`. It floats over the viewport by default:
  drag its title bar to move it, click the chevron to collapse it. Scene nodes
  get the usual visibility toggles, expand/collapse and props editor.
* :meth:`viser.GuiSceneTreeHandle.add_item` adds custom items (unique id, name,
  optional icon, optional parent) and returns a
  :class:`viser.SceneTreeItemHandle` to edit (``name``, ``icon``, ``visible``)
  or ``remove()`` them.
* ``add_menu_item`` adds right-click menu entries: on one item
  (:meth:`viser.SceneTreeItemHandle.add_menu_item`), on every item
  (:meth:`viser.GuiSceneTreeHandle.add_menu_item`), or nested in another entry
  to build a submenu (:meth:`viser.SceneTreeMenuItemHandle.add_menu_item`).
* Callbacks report browser interactions (``on_click``,
  ``on_visibility_change``, ``on_rename``, ``on_remove``, ``on_menu_click``)
  per item or for the whole tree; ``on_change`` reports every add/edit/remove
  from either side.

In this demo the "Parts" items drive the visibility of matching meshes.
Right-click "Parts" > Add > Box / Sphere to append a new part, or "Toolpaths"
> Add > Pass to append a pass. The control panel adds, renames and removes
items from Python.
"""

import time

import viser


def main() -> None:
    server = viser.ViserServer()
    server.scene.add_grid("/ground", width=6, height=6)

    meshes = {
        "box": server.scene.add_box(
            "/parts/box", dimensions=(0.6, 0.6, 0.6), color=(200, 90, 60)
        ),
        "sphere": server.scene.add_icosphere(
            "/parts/sphere", radius=0.35, position=(1.2, 0.0, 0.3)
        ),
    }

    tree = server.gui.add_scene_tree("Project", position="top-left", width=300)
    status = server.gui.add_markdown("Click an item in the scene tree.")

    parts = tree.add_item("Parts", icon=viser.Icon.BOX, item_id="parts")
    for key in meshes:
        parts.add_item(
            key.capitalize(),
            icon=viser.Icon.CUBE,
            item_id=key,
            editable=True,
            removable=True,
        )
    toolpaths = tree.add_item("Toolpaths", icon=viser.Icon.ROUTE, item_id="paths")
    toolpaths.add_item("Pass 1", editable=True, removable=True)

    def _sync_mesh(item: viser.SceneTreeItemHandle) -> None:
        """Mirror an item's (effective) visibility onto its mesh."""
        if item.item_id in meshes:
            meshes[item.item_id].visible = item.visible and parts.visible

    @tree.on_visibility_change
    def _(event: viser.SceneTreeItemEvent) -> None:
        if event.target is parts:
            for child in parts.children:
                _sync_mesh(child)
        else:
            _sync_mesh(event.target)

    @tree.on_click
    def _(event: viser.SceneTreeItemEvent) -> None:
        status.content = f"Selected **{event.target.name}**"

    @tree.on_change
    def _(event: viser.SceneTreeItemEvent) -> None:
        origin = "python" if event.client is None else f"client {event.client_id}"
        print(f"[{origin}] {event.action}: {event.target.name!r}")
        # Removals from the browser (trash icon) and from Python both land here.
        if event.action == "remove":
            mesh = meshes.pop(event.item_id, None)
            if mesh is not None:
                mesh.remove()

    # Right-click menus. Entries on an item show on that item only; entries on
    # the tree show on every item. Nesting entries makes a submenu.
    counter = {"n": 1}

    def add_part(kind: str) -> None:
        counter["n"] += 1
        key = f"{kind}-{counter['n']}"
        position = (1.2 * (len(meshes) % 4), 1.2 * (len(meshes) // 4), 0.3)
        if kind == "box":
            meshes[key] = server.scene.add_box(
                f"/parts/{key}", dimensions=(0.5, 0.5, 0.5), position=position
            )
        else:
            meshes[key] = server.scene.add_icosphere(
                f"/parts/{key}", radius=0.3, position=position
            )
        parts.add_item(
            f"{kind.capitalize()} {counter['n']}",
            icon=viser.Icon.CUBE,
            item_id=key,
            editable=True,
            removable=True,
        )
        parts.expanded = True

    parts_add = parts.add_menu_item("Add", icon=viser.Icon.PLUS)
    parts_add.add_menu_item("Box", icon=viser.Icon.BOX).on_click(
        lambda _: add_part("box")
    )
    parts_add.add_menu_item("Sphere", icon=viser.Icon.CIRCLE).on_click(
        lambda _: add_part("sphere")
    )

    paths_add = toolpaths.add_menu_item("Add", icon=viser.Icon.PLUS)
    add_pass = paths_add.add_menu_item("Pass", icon=viser.Icon.ROUTE)

    @add_pass.on_click
    def _(event: viser.SceneTreeItemEvent) -> None:
        # `event.target` is the item that was right-clicked.
        counter["n"] += 1
        event.target.add_item(f"Pass {counter['n']}", editable=True, removable=True)

    show_info = tree.add_menu_item("Show info", icon=viser.Icon.INFO_CIRCLE)

    @show_info.on_click
    def _(event: viser.SceneTreeItemEvent) -> None:
        item = event.target
        status.content = (
            f"**{item.name}** (`{item.item_id}`): {len(item.children)} children, "
            f"{'visible' if item.visible else 'hidden'}"
        )

    # Python-side factory API.
    add_button = server.gui.add_button("Add toolpath", icon=viser.Icon.PLUS)
    rename_button = server.gui.add_button("Rename selected", icon=viser.Icon.PENCIL)
    remove_button = server.gui.add_button("Remove selected", icon=viser.Icon.TRASH)
    opacity = server.gui.add_slider(
        "Panel opacity", min=0.0, max=1.0, step=0.05, initial_value=0.0
    )
    show_scene = server.gui.add_checkbox("Show scene nodes", True)

    @add_button.on_click
    def _(_) -> None:
        counter["n"] += 1
        toolpaths.add_item(f"Pass {counter['n']}", editable=True, removable=True)

    @rename_button.on_click
    def _(_) -> None:
        if tree.selected is not None:
            tree.selected.name = tree.selected.name + " *"

    @remove_button.on_click
    def _(_) -> None:
        if tree.selected is not None:
            tree.selected.remove()

    @opacity.on_update
    def _(_) -> None:
        tree.background_opacity = opacity.value

    @show_scene.on_update
    def _(_) -> None:
        tree.show_scene_nodes = show_scene.value

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    main()
