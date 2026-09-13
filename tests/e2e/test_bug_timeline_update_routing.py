"""Timeline value/visibility updates from the server must reach the client.

The timeline widget lives in its own GUI store slice (``useGui().timeline``),
not in the per-component config store. Its updates nevertheless travel as
``GuiUpdateMessage(uuid="__timeline__", ...)``, so the client's batched
GuiUpdateMessage handler has to route that uuid to the timeline slice
explicitly -- it matches neither ``panels`` nor a ``useGuiConfig`` entry.

Without that routing the updates are silently dropped: toggling
``timeline.visible`` leaves the bottom bar on screen, and assigning
``timeline.value`` (e.g. from an ``on_play`` playback loop) leaves the slider
pinned at its initial position.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

import viser


def _timeline_slider(page: Page):
    """The timeline's Mantine slider (the only one in the fixed bottom bar)."""
    return page.locator('[role="slider"]').last


def test_timeline_visible_toggle_reaches_client(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    """Assigning ``timeline.visible`` shows/hides the bottom timeline bar."""
    timeline = viser_server.add_timeline(
        min=0.0, max=100.0, step=1.0, initial_value=0.0
    )

    slider = _timeline_slider(viser_page)
    expect(slider).to_be_visible()

    timeline.visible = False
    expect(slider).not_to_be_visible()

    timeline.visible = True
    expect(slider).to_be_visible()


def test_timeline_value_assignment_moves_slider(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    """Assigning ``timeline.value`` server-side moves the rendered slider.

    This is what an ``on_play`` playback loop does on every tick; if the
    update is dropped the slider stays at its initial value.
    """
    timeline = viser_server.add_timeline(
        min=0.0, max=100.0, step=1.0, initial_value=0.0
    )

    slider = _timeline_slider(viser_page)
    expect(slider).to_have_attribute("aria-valuenow", "0")

    timeline.value = 42.0
    expect(slider).to_have_attribute("aria-valuenow", "42")

    # A second assignment must also land (the playback loop steps repeatedly).
    timeline.value = 43.0
    expect(slider).to_have_attribute("aria-valuenow", "43")


def test_play_button_reports_play_and_pause(
    viser_server: viser.ViserServer, viser_page: Page
) -> None:
    """The play/pause button is a toggle, so it must report *which* it is.

    The client sends the new toggle state as ``play``; ``timeline.playing``
    exposes it to ``on_play`` callbacks. Without it every click looks like a
    play and a playback loop can never be stopped.
    """
    timeline = viser_server.add_timeline(
        min=0.0, max=100.0, step=1.0, initial_value=0.0
    )

    states: list[bool] = []

    @timeline.on_play
    def _(event) -> None:
        states.append(timeline.playing)

    play = viser_page.get_by_role("button", name="Play")
    pause = viser_page.get_by_role("button", name="Pause")

    play.click()
    expect(pause).to_be_visible()  # icon flipped -> first click registered

    pause.click()
    expect(play).to_be_visible()  # flipped back -> second click registered

    play.click()
    expect(pause).to_be_visible()

    viser_page.wait_for_timeout(500)
    assert states == [True, False, True], states
    assert timeline.playing is True
