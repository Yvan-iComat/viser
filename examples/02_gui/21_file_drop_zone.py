"""File drop zone

Accept files dragged from the desktop onto the 3D viewer.

* :meth:`viser.GuiApi.add_file_drop_zone` draws a dashed rectangle over the
  viewer area, with a text in its center, and returns a
  :class:`viser.FileDropZoneHandle`. Only files released inside the rectangle
  and matching ``accepted_extensions`` are accepted.
* :meth:`viser.FileDropZoneHandle.on_drop` attaches a callback, called once
  per dropped file with its name and contents.

In this demo, dropped STL/OBJ meshes are added to the scene; STEP files are
only acknowledged (loading them needs a CAD kernel). The GUI controls toggle
the dashed rectangle and enable/disable the zone.
"""

import io
import time
from pathlib import PurePath

import trimesh

import viser


def main() -> None:
    server = viser.ViserServer()
    server.scene.add_grid("ground", width=10, height=10)

    zone = server.gui.add_file_drop_zone(
        accepted_extensions=(".step", ".stp", ".stl", ".obj"),
        text="Drag and drop files here.",
        show_border=True,
    )

    show_border = server.gui.add_checkbox("Show drop zone border", True)
    enabled = server.gui.add_checkbox("Accept dropped files", True)
    dropped = server.gui.add_markdown("_No files dropped yet._")

    @show_border.on_update
    def _(_) -> None:
        zone.show_border = show_border.value

    @enabled.on_update
    def _(_) -> None:
        zone.disabled = not enabled.value

    names: list[str] = []

    @zone.on_drop
    def _(event: viser.FileDropEvent) -> None:
        file = event.file
        suffix = PurePath(file.name).suffix.lower()
        names.append(f"`{file.name}` ({len(file.content) / 1024:.1f} KiB)")
        dropped.content = "\n".join(f"- {n}" for n in names)

        if suffix in (".stl", ".obj"):
            mesh = trimesh.load(
                io.BytesIO(file.content), file_type=suffix[1:], force="mesh"
            )
            assert isinstance(mesh, trimesh.Trimesh)
            server.scene.add_mesh_trimesh(f"/dropped/{PurePath(file.name).stem}", mesh)
            message = f"Added mesh with {len(mesh.faces)} faces to the scene."
        else:
            message = "STEP file received (not rendered in this demo)."

        assert event.client is not None
        event.client.add_notification(
            title=f"Dropped {file.name}",
            body=message,
            auto_close_seconds=4.0,
        )

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    main()
