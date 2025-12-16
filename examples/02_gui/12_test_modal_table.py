"""Test Modal Dialog with Table Data

Simple test script to demonstrate a modal dialog with medium size
containing a table with 6 columns.
"""

import time

import viser


def main():
    server = viser.ViserServer()

    @server.on_client_connect
    def _(client: viser.ClientHandle) -> None:
        # Create a button to open the modal
        open_modal_button = client.gui.add_button("Open Modal with Table")

        @open_modal_button.on_click
        def _(_) -> None:
            # Create modal with medium size (default)
            modal_size = "xl"
            with client.gui.add_modal(f"Data Table - {modal_size.upper()}", size=modal_size) as modal:
                client.gui.add_markdown("**Sample Data Table with 6 Columns**")

                # Create table with 6 columns
                _ = client.gui.add_table_data(
                    "Sample Data",
                    columns=[
                        ("ID", "number", False),           # Column 1: Read-only ID
                        ("Name", "string", True),          # Column 2: Editable name
                        ("Category", "string", True),      # Column 3: Editable category
                        ("Value", "number", True),         # Column 4: Editable value
                        ("Status", "string", True),        # Column 5: Editable status
                        ("Notes", "string", True),         # Column 6: Editable notes
                    ],
                    initial_rows=[
                        (1, "Item Alpha", "Type A", 125.50, "Active", "First entry"),
                        (2, "Item Beta", "Type B", 89.75, "Pending", "Second entry"),
                        (3, "Item Gamma", "Type A", 203.20, "Active", "Third entry"),
                        (4, "Item Delta", "Type C", 56.90, "Inactive", "Fourth entry"),
                        (5, "Item Epsilon", "Type B", 178.45, "Active", "Fifth entry"),
                    ],
                    selection_mode="single",
                    hint="Click cells to edit, click rows to select",
                )

                # Add close button
                close_button = client.gui.add_button("Close Modal")

                @close_button.on_click
                def _(_) -> None:
                    modal.close()

    print("Test server running!")
    print("Open http://localhost:8080 in your browser")
    print("Click the button to open a modal dialog with a 6-column table")

    while True:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
