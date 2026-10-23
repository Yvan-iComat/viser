"""Tests for file drop zones (``GuiApi.add_file_drop_zone``)."""

from __future__ import annotations

import threading
import warnings
from typing import Any
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


def _drop_file(
    server: viser.ViserServer,
    zone_uuid: str,
    filename: str,
    content: bytes,
    client_id: int = 0,
    chunk_size: int = 4,
) -> None:
    """Simulate the client's chunked upload of a dropped file."""
    server._connected_clients[ClientId(client_id)] = MagicMock(name="fake-client")
    chunks = [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)]
    server.gui._handle_file_transfer_start(
        ClientId(client_id),
        _messages.FileTransferStartUpload(
            source_component_uuid=zone_uuid,
            transfer_uuid=f"transfer-{filename}",
            filename=filename,
            mime_type="application/octet-stream",
            part_count=len(chunks),
            size_bytes=len(content),
        ),
    )
    for index, chunk in enumerate(chunks):
        server.gui._handle_file_transfer_part(
            ClientId(client_id),
            _messages.FileTransferPart(
                source_component_uuid=zone_uuid,
                transfer_uuid=f"transfer-{filename}",
                part_index=index,
                content=chunk,
            ),
        )


def _collect_drops(zone: viser.FileDropZoneHandle) -> tuple[list, threading.Event]:
    received: list[viser.FileDropEvent] = []
    event = threading.Event()

    @zone.on_drop
    def _(drop: viser.FileDropEvent) -> None:
        received.append(drop)
        event.set()

    return received, event


def test_add_drop_zone_sends_register_message_with_normalized_extensions() -> None:
    server = viser.ViserServer()
    sent = _capture_messages(server)
    zone = server.gui.add_file_drop_zone(
        ["STEP", ".stp", ".Stl", "obj", ".step"], show_border=False
    )

    registers = [
        m for m in sent if isinstance(m, _messages.RegisterFileDropZoneMessage)
    ]
    assert len(registers) == 1
    assert registers[0].uuid == zone._impl.uuid
    assert registers[0].props.accepted_extensions == (".step", ".stp", ".stl", ".obj")
    assert registers[0].props.text == "Drag and drop files here."
    assert registers[0].props.show_border is False
    assert zone.accepted_extensions == (".step", ".stp", ".stl", ".obj")


def test_invalid_extension_raises() -> None:
    server = viser.ViserServer()
    with pytest.raises(ValueError):
        server.gui.add_file_drop_zone([""])


def test_bare_string_extension_is_not_split() -> None:
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone(".stl")  # type: ignore[arg-type]
    assert zone.accepted_extensions == (".stl",)


def test_prop_assignment_sends_update_and_normalizes_extensions() -> None:
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone([".stl"])
    sent = _capture_messages(server)

    zone.text = "Drop CAD files"
    zone.accepted_extensions = ("OBJ",)

    updates = [m for m in sent if isinstance(m, _messages.FileDropZoneUpdateMessage)]
    assert {"text": "Drop CAD files"} in [u.updates for u in updates]
    assert updates[-1].updates == {"accepted_extensions": (".obj",)}
    assert zone.accepted_extensions == (".obj",)


def test_drop_accepted_file_fires_callback() -> None:
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone([".stl", ".step"])
    received, fired = _collect_drops(zone)

    _drop_file(server, zone._impl.uuid, "Part.STEP", b"ISO-10303-21;")

    assert fired.wait(timeout=5.0)
    assert len(received) == 1
    assert received[0].target is zone
    assert received[0].client_id == 0
    assert received[0].file.name == "Part.STEP"
    assert received[0].file.content == b"ISO-10303-21;"


def test_drop_zero_byte_file_fires_callback() -> None:
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone([".obj"])
    received, fired = _collect_drops(zone)

    _drop_file(server, zone._impl.uuid, "empty.obj", b"")

    assert fired.wait(timeout=5.0)
    assert received[0].file.content == b""


def test_server_rejects_unaccepted_extension() -> None:
    """A client bypassing its own filter must not reach the callback."""
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone([".stl"])
    received, fired = _collect_drops(zone)

    _drop_file(server, zone._impl.uuid, "evil.exe", b"MZ")

    assert not fired.wait(timeout=0.3)
    assert received == []
    assert server.gui._current_file_upload_states == {}


def test_empty_extension_list_accepts_everything() -> None:
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone()
    received, fired = _collect_drops(zone)

    _drop_file(server, zone._impl.uuid, "notes.txt", b"hello")

    assert fired.wait(timeout=5.0)
    assert received[0].file.name == "notes.txt"


def test_disabled_zone_ignores_drops() -> None:
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone([".stl"], disabled=True)
    received, fired = _collect_drops(zone)

    _drop_file(server, zone._impl.uuid, "part.stl", b"solid")

    assert not fired.wait(timeout=0.3)
    assert received == []


def test_remove_sends_remove_message_and_blocks_further_use() -> None:
    server = viser.ViserServer()
    zone = server.gui.add_file_drop_zone([".stl"])
    sent = _capture_messages(server)

    zone.remove()

    assert any(
        isinstance(m, _messages.RemoveFileDropZoneMessage) and m.uuid == zone._impl.uuid
        for m in sent
    )
    assert zone._impl.uuid not in server.gui._file_drop_zone_from_uuid
    with pytest.raises(RuntimeError):
        zone.text = "nope"
    with pytest.raises(RuntimeError):
        zone.on_drop(lambda _: None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        zone.remove()
    assert len(caught) == 1


def test_gui_reset_removes_drop_zones() -> None:
    server = viser.ViserServer()
    server.gui.add_file_drop_zone([".stl"])
    server.gui.add_file_drop_zone([".obj"])

    server.gui.reset()

    assert server.gui._file_drop_zone_from_uuid == {}
