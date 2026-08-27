"""Horizontal Toolbar Example

This example demonstrates the horizontal toolbar that appears at the top-middle
of the main 3D viewer window. The default toolbar contains these action buttons:
- Fit Model in View: Reframe the camera to fit the scene (handled client-side)
- Zoom In / Zoom Out: Dolly the camera toward/away from the orbit target
  (handled client-side)
- Snapshot: Capture current view as image
"""

import time

import viser


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

    # Register handler for toolbar actions
    @server.on_client_connect
    def _(client: viser.ClientHandle) -> None:
        @client.on_toolbar_action
        def handle_toolbar_action(action: str) -> None:
            """Handle toolbar button clicks."""
            print(f"Toolbar action received from {client.client_id}: {action}")

            if action == "reframe_view":
                # Reset camera to default position and FOV
                client.camera.position = (5.0, 5.0, 5.0)
                client.camera.look_at = (0.0, 0.0, 0.0)
                client.camera.fov = 50.0  # Reset FOV to default
                client.add_notification(
                    title="Fit Model in View",
                    body="Camera reset to default position",
                    auto_close_seconds=2.0,
                )

            elif action in ("zoom_in", "zoom_out"):
                # Zooming itself is handled client-side (camera dolly); the
                # server is still notified so custom logic can hook in here.
                client.add_notification(
                    title="Zoom In" if action == "zoom_in" else "Zoom Out",
                    body="Camera dollied "
                    + ("closer" if action == "zoom_in" else "farther"),
                    auto_close_seconds=2.0,
                )

            elif action == "perspective_view":
                # Note: Viser doesn't have direct orthographic mode control
                # This is a placeholder for future functionality
                client.add_notification(
                    title="Perspective View",
                    body="Perspective projection active",
                    auto_close_seconds=2.0,
                )

            elif action == "snapshot":
                # Take a snapshot (save current camera state)
                snapshot_data = {
                    "position": client.camera.position.tolist(),
                    "wxyz": client.camera.wxyz.tolist(),
                    "fov": client.camera.fov,
                    "look_at": client.camera.look_at.tolist(),
                }
                print(f"Snapshot data: {snapshot_data}")
                client.add_notification(
                    title="Snapshot Captured",
                    body="Camera state saved to console",
                    auto_close_seconds=2.0,
                    color="green",
                )

    print("Server started! The horizontal toolbar appears at the top of the 3D view.")
    print("Click the toolbar buttons to trigger actions.")

    while True:
        time.sleep(1.0)


if __name__ == "__main__":
    main()
