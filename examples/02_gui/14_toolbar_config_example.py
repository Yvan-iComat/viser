"""Toolbar Configuration Example

This example demonstrates how to configure the horizontal toolbar using the
configure_toolbar() method from the GUI API. You can:
- Control the exact order of all buttons (default and custom)
- Show/hide the toolbar
- Use default buttons (Fit Model, Snapshot) when buttons=[]
- Handle button clicks via the on_toolbar_action callback
"""

import time

import viser
from viser._messages import ToolbarButton


def main():
    server = viser.ViserServer()

    # Add some 3D content to visualize
    server.scene.add_grid("ground", width=10, height=10, cell_size=1.0)
    server.scene.add_box(
        "box1",
        position=(0, 0, 0.5),
        dimensions=(1.0, 1.0, 1.0),
        color=(255, 100, 100),
    )
    server.scene.add_box(
        "box2",
        position=(2, 0, 0.5),
        dimensions=(1.0, 1.0, 1.0),
        color=(100, 255, 100),
    )
    server.scene.add_box(
        "box3",
        position=(-2, 0, 0.5),
        dimensions=(1.0, 1.0, 1.0),
        color=(100, 100, 255),
    )

    # Configure toolbar with custom button ordering
    # Buttons appear in the exact order specified
    # If buttons=[] or omitted, default buttons (Fit Model, Snapshot) are shown
    server.gui.configure_toolbar(
        visible=True,
        buttons=[
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
        ],
    )

    # Add GUI controls to demonstrate toolbar configuration
    with server.gui.add_folder("Toolbar Settings"):
        toolbar_visible = server.gui.add_checkbox(
            "Show Toolbar",
            initial_value=True,
        )

        @toolbar_visible.on_update
        def _(_) -> None:
            server.gui.configure_toolbar(
                visible=toolbar_visible.value,
                buttons=[
                    ToolbarButton(
                        action="toggle_grid",
                        icon="IconGrid3x3",
                        tooltip="Toggle grid visibility",
                    ),
                    ToolbarButton(
                        action="reframe_view",
                        icon="IconMaximize",
                        tooltip="Fit model in view",
                    ),
                    ToolbarButton(
                        action="reset_scene",
                        icon="IconRefresh",
                        tooltip="Reset scene to default",
                    ),
                    ToolbarButton(
                        action="snapshot",
                        icon="IconCamera",
                        tooltip="Take snapshot",
                    ),
                ],
            )

    # Track grid visibility state
    grid_visible = True

    # Register handler for toolbar actions
    @server.on_client_connect
    def _(client: viser.ClientHandle) -> None:
        nonlocal grid_visible

        @client.on_toolbar_action
        def handle_toolbar_action(action: str) -> None:
            """Handle both default and custom toolbar button clicks."""
            print(f"Toolbar action received from {client.client_id}: {action}")

            # Handle default toolbar actions
            if action == "reframe_view":
                # Simple fit: position camera to see all boxes
                client.camera.position = (6.0, 6.0, 6.0)
                client.camera.look_at = (0.0, 0.0, 0.5)

            elif action == "snapshot":
                # Simple snapshot: just print camera position
                print(f"Camera: pos={client.camera.position}, look_at={client.camera.look_at}")

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
                    server.scene.remove_node("ground")
                client.add_notification(
                    title="Grid Toggled",
                    body=f"Grid {'visible' if grid_visible else 'hidden'}",
                    auto_close_seconds=2.0,
                )

            elif action == "reset_scene":
                print("Resetting scene...")
                # Remove all boxes
                for i in range(1, 4):
                    try:
                        server.scene.remove_node(f"box{i}")
                    except:
                        pass
                # Re-add boxes at default positions
                server.scene.add_box(
                    "box1",
                    position=(0, 0, 0.5),
                    dimensions=(1.0, 1.0, 1.0),
                    color=(255, 100, 100),
                )
                server.scene.add_box(
                    "box2",
                    position=(2, 0, 0.5),
                    dimensions=(1.0, 1.0, 1.0),
                    color=(100, 255, 100),
                )
                server.scene.add_box(
                    "box3",
                    position=(-2, 0, 0.5),
                    dimensions=(1.0, 1.0, 1.0),
                    color=(100, 100, 255),
                )
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
    print("   - Click toolbar buttons to trigger actions")
    print("   - Check console output for action logs")
    print("=" * 70)

    while True:
        time.sleep(1.0)


if __name__ == "__main__":
    main()
