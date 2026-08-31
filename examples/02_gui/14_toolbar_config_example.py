"""Toolbar Configuration Example

This example demonstrates how to configure the horizontal toolbar using the
configure_toolbar() method from the GUI API. You can:
- Control the exact order of all buttons (default and custom)
- Show/hide the toolbar
- Use default buttons (Fit Model, Snapshot) when buttons=[]
- Handle button clicks via the on_toolbar_action callback
- Control the toolbar's distance from the top of the window via top_offset

The scene holds boxes, a sphere and a cone inside a much larger 10x10 grid, so
it also serves as a check on "fit all in": that button frames the meshes only,
so the model should fill the view instead of being dwarfed by the grid.
"""

import random
import time

import trimesh

import viser
from viser._messages import ToolbarButton

# Every mesh add_scene_content() creates. "Reset scene" removes exactly these
# and rebuilds them, so the two must stay in sync.
MESH_NAMES = ("box1", "box2", "box3", "sphere", "cone")


def add_scene_content(server: viser.ViserServer) -> dict[str, object]:
    """Populate the scene with the meshes used to exercise "fit all in".

    The meshes are deliberately much smaller than the 10x10 grid: "fit all in"
    frames the scene's real geometry, so the model should fill the view rather
    than being framed down to a speck by the grid's extent.

    Returns the mesh handles keyed by name, so callers can move them (see the
    "Scatter meshes" button) without reaching into scene-API internals.
    """
    handles: dict[str, object] = {}
    handles["box1"] = server.scene.add_box(
        "box1",
        position=(0, 0, 0.5),
        dimensions=(1.0, 1.0, 1.0),
        color=(255, 100, 100),
    )
    handles["box2"] = server.scene.add_box(
        "box2",
        position=(2, 0, 0.5),
        dimensions=(1.0, 1.0, 1.0),
        color=(100, 255, 100),
    )
    handles["box3"] = server.scene.add_box(
        "box3",
        position=(-2, 0, 0.5),
        dimensions=(1.0, 1.0, 1.0),
        color=(100, 100, 255),
    )
    handles["sphere"] = server.scene.add_icosphere(
        "sphere",
        radius=0.6,
        position=(0, 2.0, 0.6),
        color=(255, 220, 90),
    )
    # No add_cone() in the scene API (add_cylinder takes a single radius), so
    # build the cone with trimesh. Its origin is at the centroid, hence the
    # height/2 lift to sit the base on the ground plane.
    cone_height = 1.5
    handles["cone"] = server.scene.add_mesh_trimesh(
        "cone",
        trimesh.creation.cone(radius=0.6, height=cone_height),
        position=(0, -2.0, cone_height / 2),
    )
    return handles


def main():
    server = viser.ViserServer()

    # Add some 3D content to visualize
    server.scene.add_grid("ground", width=10, height=10, cell_size=1.0)
    # Live mesh handles; replaced wholesale whenever the scene is rebuilt.
    mesh_handles = add_scene_content(server)

    # Buttons appear in the exact order specified. If buttons=[] or omitted,
    # the default buttons (Fit Model, Zoom, Snapshot) are shown instead.
    buttons = [
        ToolbarButton(
            action="toggle_grid",
            icon="IconGrid3x3",
            tooltip="Toggle grid visibility",
        ),
        ToolbarButton(
            action="reframe_view",  # Default action, custom position
            icon="IconMaximize",
            tooltip="Fit model in view",
        ),
        ToolbarButton(
            action="reset_scene",
            icon="IconRefresh",
            tooltip="Reset scene to default",
        ),
        ToolbarButton(
            action="snapshot",  # Default action, custom position
            icon="IconCamera",
            tooltip="Take snapshot",
        ),
    ]

    # Add GUI controls to demonstrate toolbar configuration
    with server.gui.add_folder("Toolbar Settings"):
        toolbar_visible = server.gui.add_checkbox(
            "Show Toolbar",
            initial_value=True,
        )
        # Vertical placement, in em. 4.0 sits just below the titlebar; a string
        # such as "50%" or "20px" would also work.
        toolbar_top_offset = server.gui.add_slider(
            "Toolbar Top Offset (em)",
            min=0.0,
            max=20.0,
            step=0.5,
            initial_value=4.0,
        )

        def update_toolbar() -> None:
            server.gui.configure_toolbar(
                visible=toolbar_visible.value,
                buttons=buttons,
                top_offset=toolbar_top_offset.value,
            )

        toolbar_visible.on_update(lambda _: update_toolbar())
        toolbar_top_offset.on_update(lambda _: update_toolbar())

    # Push the initial configuration (buttons, visibility, placement).
    update_toolbar()

    # "Reset scene to default" only shows up as a change if the scene has
    # actually been modified, so give the user a way to disturb it first.
    with server.gui.add_folder("Scene"):
        scatter_button = server.gui.add_button("Scatter meshes")

        @scatter_button.on_click
        def _(_) -> None:
            for handle in mesh_handles.values():
                handle.position = (
                    random.uniform(-4.0, 4.0),
                    random.uniform(-4.0, 4.0),
                    random.uniform(0.5, 3.0),
                )
            print("Scattered meshes -- now click 'Reset scene' to restore them.")

    # Track grid visibility state
    grid_visible = True

    # Register handler for toolbar actions
    @server.on_client_connect
    def _(client: viser.ClientHandle) -> None:
        nonlocal grid_visible

        @client.on_toolbar_action
        def handle_toolbar_action(action: str) -> None:
            """Handle both default and custom toolbar button clicks."""
            nonlocal grid_visible, mesh_handles
            print(f"Toolbar action received from {client.client_id}: {action}")

            # Handle default toolbar actions
            if action == "reframe_view":
                # NOTE: deliberately no camera move here. "fit all in" is
                # handled entirely client-side (it frames the bounding sphere
                # of the scene's meshes, excluding the grid). Setting the
                # camera here would fight that and mask the real behavior --
                # which is exactly what this example is for testing.
                print(f"Camera after fit: pos={client.camera.position}")

            elif action == "snapshot":
                # Simple snapshot: just print camera position
                print(
                    f"Camera: pos={client.camera.position}, look_at={client.camera.look_at}"
                )

            # Handle custom toolbar actions
            elif action == "save_scene":
                print("Saving scene...")
                client.add_notification(
                    title="Scene Saved",
                    body="Scene data saved successfully",
                    auto_close_seconds=2.0,
                    color="green",
                )

            elif action == "export_data":
                print("Exporting data...")
                client.add_notification(
                    title="Export Started",
                    body="Exporting scene data to file...",
                    auto_close_seconds=2.0,
                    color="blue",
                )

            elif action == "toggle_grid":
                grid_visible = not grid_visible
                if grid_visible:
                    server.scene.add_grid("ground", width=10, height=10, cell_size=1.0)
                else:
                    server.scene.remove_by_name("ground")
                client.add_notification(
                    title="Grid Toggled",
                    body=f"Grid {'visible' if grid_visible else 'hidden'}",
                    auto_close_seconds=2.0,
                )

            elif action == "reset_scene":
                print("Resetting scene...")
                for mesh_name in MESH_NAMES:
                    try:
                        server.scene.remove_by_name(mesh_name)
                    except KeyError:
                        pass
                # Re-add via the same helper used at startup, so a reset can't
                # drift from the initial scene. Rebinding the handles matters:
                # the old ones refer to removed nodes, so "Scatter meshes"
                # would silently stop working after a reset. Note the reset is
                # only *visible* once the scene has been changed -- click
                # "Scatter meshes" first, then reset to watch them snap back.
                mesh_handles = add_scene_content(server)
                client.add_notification(
                    title="Scene Reset",
                    body="Scene restored to default state",
                    auto_close_seconds=2.0,
                    color="orange",
                )

    print("=" * 70)
    print("TOOLBAR CONFIGURATION EXAMPLE")
    print("=" * 70)
    print("\nServer running at: http://localhost:8080")
    print("\nTOOLBAR FEATURES:")
    print("   - Full control over button order via buttons array")
    print("   - Default buttons (Fit Model, Snapshot) shown if buttons=[]")
    print("   - Current order: Toggle Grid, Fit Model, Reset Scene, Snapshot")
    print("\nCONTROLS:")
    print("   - Use the 'Show Toolbar' checkbox to toggle toolbar visibility")
    print("   - Use the 'Toolbar Top Offset' slider to move the toolbar down")
    print("   - Click toolbar buttons to trigger actions")
    print("   - Check console output for action logs")
    print("\nRESET SCENE:")
    print("   - Click 'Scatter meshes' (Scene folder) to move the meshes")
    print("   - Then click 'Reset scene' -- they snap back to their defaults")
    print("   - Reset on an untouched scene is a no-op, by design")
    print("\nFIT ALL IN:")
    print("   - Scene: 3 boxes + sphere + cone, inside a 10x10 grid")
    print("   - Click 'Fit model' -- the meshes should fill the view")
    print("   - Grid/lights/gizmos are excluded, so the fit tracks the meshes")
    print("   - Toggle the grid off and refit: framing should not change")
    print("=" * 70)

    while True:
        time.sleep(1.0)


if __name__ == "__main__":
    main()
