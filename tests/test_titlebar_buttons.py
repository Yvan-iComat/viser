"""Tests for custom titlebar buttons and separators
(``GuiApi.add_titlebar_button`` / ``GuiApi.add_titlebar_separator``)."""

from __future__ import annotations

import asyncio
import threading
import warnings
from typing import Any, Coroutine
from unittest.mock import MagicMock, patch

import pytest

import viser
import viser._client_autobuild
from viser import _messages
from viser.infra import ClientId

pytestmark = pytest.mark.usefixtures("_no_client_build")


@pytest.fixture
def _no_client_build():
    with patch.object(viser._client_autobuild, "ensure_client_is_built", lambda: None):
        yield


def _capture_messages(server: viser.ViserServer) -> list[Any]:
    sent: list[Any] = []
    original_queue = server._websock_server.queue_message

    def capture_queue(message):
        sent.append(message)
        return original_queue(message)

    server._websock_server.queue_message = capture_queue  # type: ignore
    return sent


def _run_coro(coro: Coroutine[Any, Any, Any]) -> None:
    """Run a coroutine on a fresh event loop in a worker thread (see
    test_modifier_filtering.py for why ``asyncio.run`` isn't used)."""
    error: list[BaseException] = []

    def _target() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        except BaseException as e:
            error.append(e)
        finally:
            loop.close()

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if error:
        raise error[0]


def _click(server: viser.ViserServer, uuid: str, client_id: int = 0) -> None:
    server._connected_clients[ClientId(client_id)] = MagicMock(name="fake-client")
    _run_coro(
        server.gui._handle_titlebar_button_click(
            ClientId(client_id), _messages.TitlebarButtonClickMessage(uuid=uuid)
        )
    )


def test_add_button_requires_exactly_one_of_text_or_icon() -> None:
    server = viser.ViserServer()
    with pytest.raises(ValueError):
        server.gui.add_titlebar_button()
    with pytest.raises(ValueError):
        server.gui.add_titlebar_button("Both", icon=viser.Icon.CUBE)


def test_add_button_and_separator_send_register_messages() -> None:
    server = viser.ViserServer()
    sent = _capture_messages(server)

    text_btn = server.gui.add_titlebar_button("Help", tooltip="Open help")
    sep = server.gui.add_titlebar_separator()
    icon_btn = server.gui.add_titlebar_button(icon=viser.Icon.CUBE)

    msgs = [m for m in sent if isinstance(m, _messages.RegisterTitlebarItemMessage)]
    assert [m.props._kind for m in msgs] == ["button", "separator", "button"]
    assert msgs[0].props.text == "Help"
    assert msgs[0].props.tooltip == "Open help"
    assert msgs[0].props._icon_html is None
    assert msgs[2].props.text is None
    assert msgs[2].props._icon_html is not None
    # Default order follows insertion order.
    assert text_btn.order < sep.order < icon_btn.order
    assert icon_btn.icon == viser.Icon.CUBE


def test_text_and_icon_assignment_are_mutually_exclusive() -> None:
    server = viser.ViserServer()
    btn = server.gui.add_titlebar_button(icon=viser.Icon.CUBE)
    sent = _capture_messages(server)

    btn.text = "Cube"
    assert btn.text == "Cube"
    assert btn.icon is None
    assert btn._impl.props._icon_html is None

    btn.icon = viser.Icon.X
    assert btn.icon == viser.Icon.X
    assert btn.text is None

    updates = [
        m.updates for m in sent if isinstance(m, _messages.TitlebarItemUpdateMessage)
    ]
    assert {"text": "Cube"} in updates
    assert {"_icon_html": None} in updates
    assert {"text": None} in updates


def test_prop_updates_send_update_messages() -> None:
    server = viser.ViserServer()
    btn = server.gui.add_titlebar_button("Go")
    sent = _capture_messages(server)

    btn.disabled = True
    btn.visible = False
    btn.tooltip = "Tip"

    updates = [
        m.updates for m in sent if isinstance(m, _messages.TitlebarItemUpdateMessage)
    ]
    assert updates == [{"disabled": True}, {"visible": False}, {"tooltip": "Tip"}]
    assert btn.disabled is True


def test_click_dispatches_to_callbacks() -> None:
    server = viser.ViserServer()
    btn = server.gui.add_titlebar_button("Go")
    events: list[viser.TitlebarButtonEvent] = []

    @btn.on_click
    async def _(event: viser.TitlebarButtonEvent) -> None:
        events.append(event)

    _click(server, btn._impl.uuid, client_id=3)
    assert len(events) == 1
    assert events[0].target is btn
    assert events[0].client_id == 3


def test_click_ignored_when_disabled_removed_or_separator() -> None:
    server = viser.ViserServer()
    btn = server.gui.add_titlebar_button("Go", disabled=True)
    sep = server.gui.add_titlebar_separator()
    calls: list[str] = []

    @btn.on_click
    async def _(event: viser.TitlebarButtonEvent) -> None:
        calls.append("clicked")

    _click(server, btn._impl.uuid)
    _click(server, sep._impl.uuid)
    _click(server, "unknown-uuid")
    assert calls == []

    btn.disabled = False
    _click(server, btn._impl.uuid)
    assert calls == ["clicked"]

    btn.remove()
    _click(server, btn._impl.uuid)
    assert calls == ["clicked"]


def test_remove_sends_message_and_guards_writes() -> None:
    server = viser.ViserServer()
    btn = server.gui.add_titlebar_button("Go")
    sep = server.gui.add_titlebar_separator()
    sent = _capture_messages(server)

    btn.remove()
    sep.remove()
    removed = [
        m.uuid for m in sent if isinstance(m, _messages.RemoveTitlebarItemMessage)
    ]
    assert removed == [btn._impl.uuid, sep._impl.uuid]
    assert server.gui._titlebar_item_from_uuid == {}

    with pytest.raises(RuntimeError):
        btn.text = "Again"
    with pytest.raises(RuntimeError):
        btn.on_click(lambda _: None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        btn.remove()
        sep.remove()
    assert len(w) == 2
    assert all("already removed" in str(x.message) for x in w)


def test_reset_removes_titlebar_items() -> None:
    server = viser.ViserServer()
    server.gui.add_titlebar_button("A")
    server.gui.add_titlebar_separator()
    server.gui.reset()
    assert server.gui._titlebar_item_from_uuid == {}
