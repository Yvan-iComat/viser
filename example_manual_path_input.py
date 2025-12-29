"""
Example: Manual path input with validation.

Simple approach where users type or paste the absolute path directly.
Works universally but less user-friendly than a visual picker.
"""

import viser
from pathlib import Path


def create_path_input_with_validation(server: viser.ViserServer):
    """Create a validated path input GUI."""

    # Path input field
    path_input = server.gui.add_text(
        "Folder Path",
        initial_value=str(Path.home()),
        hint="Enter or paste the absolute folder path",
        multiline=False,
    )

    # Validation status
    status_display = server.gui.add_markdown(
        "**Status:** Enter a path to validate"
    )

    # Browse button (uses folder picker if available)
    browse_button = server.gui.add_folder_select_button(
        "Browse...",
        hint="Select folder (may only show folder name in browsers)",
    )

    # Validate button
    validate_button = server.gui.add_button("✓ Validate Path", color="blue")

    # Use button
    use_button = server.gui.add_button(
        "✅ Use This Path",
        color="green",
        disabled=True,
    )

    current_valid_path = None

    def validate_path(path_str: str) -> tuple[bool, str, Path | None]:
        """Validate the given path string."""
        try:
            path = Path(path_str).expanduser().resolve()

            if not path.exists():
                return False, f"❌ **Error:** Path does not exist", None

            if not path.is_dir():
                return False, f"❌ **Error:** Path is not a directory", None

            if not os.access(path, os.R_OK):
                return False, f"❌ **Error:** No read permission for this directory", None

            return True, f"✅ **Valid:** `{path}`", path

        except Exception as e:
            return False, f"❌ **Error:** {str(e)}", None

    @path_input.on_update
    def _on_path_change(event):
        """Auto-validate as user types."""
        nonlocal current_valid_path
        is_valid, message, valid_path = validate_path(event.target.value)

        status_display.content = message
        use_button.disabled = not is_valid
        current_valid_path = valid_path

    @browse_button.on_select
    def _on_browse(event):
        """Handle folder selection from browse button."""
        # Note: This may only be a folder name in some browsers
        selected = event.target.value

        # Try to resolve it relative to current path
        if current_valid_path:
            potential_path = current_valid_path / selected
            if potential_path.exists():
                path_input.value = str(potential_path)
            else:
                status_display.content = (
                    f"⚠️ **Warning:** Browser returned folder name '{selected}'. "
                    "Please enter the full path manually."
                )
        else:
            status_display.content = (
                f"⚠️ **Browser returned:** '{selected}'. Please enter the full path."
            )

    @validate_button.on_click
    def _on_validate(event):
        """Manually trigger validation."""
        nonlocal current_valid_path
        is_valid, message, valid_path = validate_path(path_input.value)

        status_display.content = message
        use_button.disabled = not is_valid
        current_valid_path = valid_path

    @use_button.on_click
    def _on_use(event):
        """Use the validated path."""
        if current_valid_path:
            print(f"Using folder: {current_valid_path}")
            print(f"Absolute path: {current_valid_path.absolute()}")

            # Do something with the path
            status_display.content = f"✅ **Selected:** `{current_valid_path}`"

            return str(current_valid_path.absolute())


if __name__ == "__main__":
    import os
    server = viser.ViserServer()

    print("Manual Path Input Example")
    print("========================")
    print("Users can type or paste absolute paths directly.")
    print("This works universally across all browsers.")
    print()

    create_path_input_with_validation(server)

    print("Server running at http://localhost:8080")

    import time
    while True:
        time.sleep(1)
