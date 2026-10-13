"""Titlebar buttons

Add custom buttons to the main titlebar, to the right of the app image, and
group them with vertical separators.

* :meth:`viser.GuiApi.add_titlebar_button` adds a button showing either an
  icon or a text label, and returns a :class:`viser.TitlebarButtonHandle`.
* :meth:`viser.TitlebarButtonHandle.on_click` attaches a click callback.
* :meth:`viser.GuiApi.add_titlebar_separator` adds a vertical separator.

In this demo, the first group of buttons toggles floating panels for the
client that clicked; the other buttons act on the camera, show notifications,
or open a modal.
"""

import base64
import time

import viser
from viser.theme import TitlebarConfig, TitlebarImage

# A small inline logo, so the demo works offline.
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="#228be6" stroke-width="2" stroke-linejoin="round">'
    '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9'
    'M12 12L4 7.5"/></svg>'
)
_LOGO_URL = "data:image/svg+xml;base64," + base64.b64encode(_LOGO_SVG.encode()).decode(
    "ascii"
)


def main() -> None:
    server = viser.ViserServer()
    server.scene.add_grid("ground", width=10, height=10)
    server.scene.add_box("box", position=(0, 0, 0.5), dimensions=(1, 1, 1))

    # The app image on the left of the titlebar; custom buttons follow it.
    server.gui.configure_theme(
        titlebar_content=TitlebarConfig(
            buttons=None,
            image=TitlebarImage(
                image_url_light=_LOGO_URL,
                image_url_dark=None,
                image_alt="Demo logo",
                href=None,
            ),
        )
    )
    server.gui.add_markdown("Use the buttons in the titlebar, next to the logo.")

    # Group 1: icon buttons that toggle floating panels. Panels are created
    # per client (`client.gui`), so each browser tab has its own.
    open_panels: dict[tuple[int, str], viser.PanelHandle] = {}

    def make_panel(client: viser.ClientHandle, name: str) -> viser.PanelHandle:
        panel = client.gui.add_panel()
        if name == "tools":
            with panel.add_tab("Tools", viser.Icon.TOOL):
                client.gui.add_slider(
                    "Size", min=0.1, max=3.0, step=0.1, initial_value=1.0
                )
                client.gui.add_rgb("Color", initial_value=(90, 140, 230))
            panel.float(x=30, y=-30, width=280)
        else:
            with panel.add_tab("Stats", viser.Icon.CHART_BAR):
                stats = client.gui.add_markdown("")
                cam = client.camera
                stats.content = (
                    f"**Client:** {client.client_id}\n\n"
                    f"**Camera position:** {tuple(round(float(v), 2) for v in cam.position)}\n\n"
                    f"**FOV:** {cam.fov:.2f} rad"
                )
            panel.float(x=-30, y=-30, width=300)
        return panel

    def toggle_panel(event: viser.TitlebarButtonEvent, name: str) -> None:
        client = event.client
        assert client is not None
        key = (client.client_id, name)
        panel = open_panels.pop(key, None)
        if panel is not None:
            panel.remove()
        else:
            open_panels[key] = make_panel(client, name)

    tools_btn = server.gui.add_titlebar_button(
        icon=viser.Icon.TOOL, tooltip="Toggle tools panel"
    )
    stats_btn = server.gui.add_titlebar_button(
        icon=viser.Icon.CHART_BAR, tooltip="Toggle stats panel"
    )
    tools_btn.on_click(lambda event: toggle_panel(event, "tools"))
    stats_btn.on_click(lambda event: toggle_panel(event, "stats"))

    server.gui.add_titlebar_separator()

    # Group 2: camera and notification actions.
    reset_btn = server.gui.add_titlebar_button(
        icon=viser.Icon.HOME, tooltip="Reset camera"
    )
    notify_btn = server.gui.add_titlebar_button(
        icon=viser.Icon.BELL, tooltip="Show a notification"
    )

    @reset_btn.on_click
    def _(event: viser.TitlebarButtonEvent) -> None:
        assert event.client is not None
        event.client.camera.position = (5.0, 5.0, 5.0)
        event.client.camera.look_at = (0.0, 0.0, 0.0)

    @notify_btn.on_click
    def _(event: viser.TitlebarButtonEvent) -> None:
        assert event.client is not None
        event.client.add_notification(
            title="Titlebar button",
            body=f"Clicked by client {event.client_id}.",
            auto_close_seconds=2.0,
        )

    server.gui.add_titlebar_separator()

    # Group 3: a text button, and a disabled button toggled from the GUI.
    help_btn = server.gui.add_titlebar_button("Help", tooltip="About this demo")
    locked_btn = server.gui.add_titlebar_button(
        icon=viser.Icon.LOCK, tooltip="Disabled until enabled", disabled=True
    )

    @help_btn.on_click
    def _(event: viser.TitlebarButtonEvent) -> None:
        assert event.client is not None
        with event.client.gui.add_modal("Titlebar buttons") as modal:
            event.client.gui.add_markdown(
                "Buttons are added with `server.gui.add_titlebar_button()` and "
                "grouped with `server.gui.add_titlebar_separator()`."
            )
            event.client.gui.add_button("Close").on_click(lambda _: modal.close())

    @locked_btn.on_click
    def _(event: viser.TitlebarButtonEvent) -> None:
        assert event.client is not None
        event.client.add_notification(
            title="Unlocked button", body="Clicked!", auto_close_seconds=2.0
        )

    # Control-panel widgets that update the titlebar buttons live.
    enable = server.gui.add_checkbox("Enable lock button", initial_value=False)
    show_stats = server.gui.add_checkbox("Show stats button", initial_value=True)
    help_label = server.gui.add_text("Help button text", initial_value="Help")

    @enable.on_update
    def _(_) -> None:
        locked_btn.disabled = not enable.value
        locked_btn.icon = viser.Icon.LOCK_OPEN if enable.value else viser.Icon.LOCK

    @show_stats.on_update
    def _(_) -> None:
        stats_btn.visible = show_stats.value

    @help_label.on_update
    def _(_) -> None:
        help_btn.text = help_label.value or "Help"

    @server.on_client_disconnect
    def _(client: viser.ClientHandle) -> None:
        for key in [k for k in open_panels if k[0] == client.client_id]:
            open_panels.pop(key)

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    main()
