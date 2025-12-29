"""Test script for the folder selection GUI control.

This demonstrates the File System Access API approach which provides
better path information in Chrome/Edge browsers.

For other approaches (server-side browser, manual input), see:
- example_server_file_browser.py
- example_manual_path_input.py
"""

import time
import viser

server = viser.ViserServer()

server.gui.add_markdown(
    """
# Folder Selection Test

**Browser Support:**
- ✅ **Chrome/Edge 86+**: Uses File System Access API (better paths)
- ⚠️ **Firefox/Safari**: Falls back to folder name only

**For guaranteed absolute paths**, see:
- `FOLDER_SELECTION_GUIDE.md`
- `example_server_file_browser.py`
"""
)

# Method 1: File System Access API button (auto-detects browser support)
folder_button = server.gui.add_folder_select_button(
    "📁 Select Folder",
    hint="Click to choose a folder",
    color="blue",
)

# Display for selected folder
folder_display = server.gui.add_text(
    "Selected Folder",
    initial_value="No folder selected",
    disabled=True,
)

# Browser info
browser_info = server.gui.add_markdown(
    "**Browser Detection:** Click the button to see what your browser provides"
)


@folder_button.on_select
def _on_folder_select(event: viser.GuiEvent) -> None:
    """Handle folder selection."""
    selected_folder = event.target.value
    print(f"Folder selected: {selected_folder}")
    folder_display.value = f"Selected: {selected_folder}"

    # Update browser info
    if "/" in selected_folder or "\\" in selected_folder:
        browser_info.content = (
            "✅ **Good!** Your browser provided path information. "
            "This likely means you're using Chrome/Edge with File System Access API."
        )
    else:
        browser_info.content = (
            "⚠️ **Limited Info:** Your browser only provided the folder name. "
            "For absolute paths, try Chrome/Edge or see `example_server_file_browser.py`"
        )


print("=" * 60)
print("Folder Selection Test")
print("=" * 60)
print("Server running at http://localhost:8080")
print()
print("What to expect:")
print("- Chrome/Edge 86+: Better path information via File System Access API")
print("- Other browsers: Folder name only (browser security limitation)")
print()
print("For guaranteed absolute paths across all browsers:")
print("- See: example_server_file_browser.py (server-side approach)")
print("- See: FOLDER_SELECTION_GUIDE.md (complete guide)")
print("=" * 60)

# Keep the server running
while True:
    time.sleep(1)
