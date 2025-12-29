"""
Example: Server-side file browser for selecting folders with absolute paths.

This approach bypasses browser security limitations by having the server
provide a list of directories that the user can browse and select.
"""

import os
import viser
from pathlib import Path


def get_subdirectories(path: Path) -> list[str]:
    """Get all subdirectories in the given path."""
    try:
        return sorted([
            d.name for d in path.iterdir()
            if d.is_dir() and not d.name.startswith('.')
        ])
    except (PermissionError, OSError):
        return []


def create_file_browser(server: viser.ViserServer, start_path: Path | None = None):
    """Create a server-side file browser GUI."""

    if start_path is None:
        start_path = Path.home()

    current_path = start_path

    # Display current path
    path_display = server.gui.add_text(
        "Current Path",
        initial_value=str(current_path),
        disabled=True,
    )

    # Folder for navigation buttons
    with server.gui.add_folder("Navigation", expand_by_default=True):
        # Up/Parent directory button
        up_button = server.gui.add_button("⬆️ Parent Directory")

        # Refresh button
        refresh_button = server.gui.add_button("🔄 Refresh")

    # Dropdown for subdirectories
    subdirs = get_subdirectories(current_path)
    if not subdirs:
        subdirs = ["<no subdirectories>"]

    folder_dropdown = server.gui.add_dropdown(
        "Select Subfolder",
        options=subdirs,
    )

    # Enter button to navigate into selected folder
    enter_button = server.gui.add_button("📂 Enter Folder")

    # Select current folder button
    select_button = server.gui.add_button(
        "✅ Select This Folder",
        color="green",
    )

    # Result display
    selected_display = server.gui.add_text(
        "Selected Folder",
        initial_value="No folder selected",
        disabled=True,
    )

    def update_dropdown():
        """Update the dropdown with current subdirectories."""
        nonlocal current_path
        subdirs = get_subdirectories(current_path)
        if not subdirs:
            subdirs = ["<no subdirectories>"]
        folder_dropdown.options = subdirs
        path_display.value = str(current_path)

    @up_button.on_click
    def _on_up(event):
        """Navigate to parent directory."""
        nonlocal current_path
        current_path = current_path.parent
        update_dropdown()

    @refresh_button.on_click
    def _on_refresh(event):
        """Refresh the current directory listing."""
        update_dropdown()

    @enter_button.on_click
    def _on_enter(event):
        """Enter the selected subfolder."""
        nonlocal current_path
        selected_folder = folder_dropdown.value
        if selected_folder != "<no subdirectories>":
            new_path = current_path / selected_folder
            if new_path.is_dir():
                current_path = new_path
                update_dropdown()

    @select_button.on_click
    def _on_select(event):
        """Select the current folder."""
        selected_display.value = str(current_path)
        print(f"Selected folder: {current_path}")
        print(f"Absolute path: {current_path.absolute()}")

        # Here you can do whatever you need with the absolute path
        # For example, process files, save to database, etc.
        return str(current_path.absolute())


if __name__ == "__main__":
    server = viser.ViserServer()

    print("Server-side File Browser Example")
    print("================================")
    print("This approach provides absolute paths by navigating the")
    print("file system on the server side, avoiding browser limitations.")
    print()

    # Start browsing from user's home directory
    # You can also use: Path("/"), Path.cwd(), or any other starting location
    create_file_browser(server, start_path=Path.home())

    print("Server running at http://localhost:8080")

    import time
    while True:
        time.sleep(1)
