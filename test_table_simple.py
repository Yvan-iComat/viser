"""Simple table test"""
import time
import viser

server = viser.ViserServer()
server.gui.configure_theme(dark_mode=False,control_width="large")

# Create a very simple table
table = server.gui.add_table_data(
    "Test Table",
    columns=["Name", "Value"],
    initial_rows=[
        ("Row 1", "Data 1"),
        ("Row 2", "Data 2"),
    ],
)

print("Server running at http://localhost:8080")
print("Table should be visible in the GUI!")

while True:
    time.sleep(1.0)
