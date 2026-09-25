"""Unit tests for the chat GUI component and its conversation store."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

import viser


def _submit(
    chat: viser.GuiChatHandle,
    text: str,
    attachments: list[viser.ChatAttachment] | None = None,
) -> None:
    """Simulate a client submission and wait for all callbacks to finish."""

    async def run() -> None:
        chat._handle_submit(None, 0, text, attachments or [])  # type: ignore[arg-type]
        await asyncio.gather(*chat._tasks)

    # Own thread: Playwright's sync API (e2e tests in the same session) leaves
    # an event loop running on the main thread, which asyncio.run() rejects.
    thread = threading.Thread(target=lambda: asyncio.run(run()))
    thread.start()
    thread.join()


def test_store_roundtrip_with_attachments(tmp_path: Path) -> None:
    store = viser.ConversationStore(tmp_path)
    conv = viser.Conversation()
    conv.append(
        viser.ChatMessage(
            "user",
            "Describe   this\nimage please",
            [viser.ChatAttachment("a b.png", "image/png", b"PNGDATA", b"THUMB")],
        )
    )
    conv.append(viser.ChatMessage("assistant", "It is a **cat**."))
    store.save(conv)

    listed = store.list_conversations()
    assert [c.conversation_id for c in listed] == [conv.conversation_id]
    assert listed[0].title == "Describe this image please"

    loaded = store.load(conv.conversation_id)
    assert loaded is not None
    assert [m.text for m in loaded.messages] == [m.text for m in conv.messages]
    att = loaded.messages[0].attachments[0]
    assert (att.name, att.mime_type, att.data, att.thumbnail) == (
        "a b.png",
        "image/png",
        b"PNGDATA",
        b"THUMB",
    )

    store.delete(conv.conversation_id)
    assert store.list_conversations() == []
    assert store.load(conv.conversation_id) is None
    assert list(tmp_path.iterdir()) == []


def test_store_rejects_path_traversal(tmp_path: Path) -> None:
    store = viser.ConversationStore(tmp_path)
    with pytest.raises(ValueError):
        store.load("../secret")


def test_add_message_and_stream() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        chat = server.gui.add_chat()
        assert chat.messages == ()
        assert chat.conversations == ()

        chat.add_message("user", "Hello")
        # First message saves the conversation and lists it in the history.
        assert [c.title for c in chat.conversations] == ["Hello"]
        assert chat.active_conversation_id == chat.conversation.conversation_id

        with chat.stream() as reply:
            reply.write("Hi ")
            assert chat.streaming_text == "Hi "
            reply.write("there!")
        assert chat.streaming_text is None
        assert [(m.role, m.text) for m in chat.messages] == [
            ("user", "Hello"),
            ("assistant", "Hi there!"),
        ]

        # An empty stream adds nothing.
        with chat.stream():
            pass
        assert len(chat.messages) == 2
    finally:
        server.stop()


def test_submit_runs_callback_with_busy_state() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        chat = server.gui.add_chat()
        seen: list[tuple[bool, list[str]]] = []

        @chat.on_submit
        def _(event: viser.ChatSubmitEvent) -> None:
            assert threading.current_thread() is not threading.main_thread()
            seen.append(
                (event.chat.busy, [m.text for m in event.conversation.messages])
            )
            event.chat.add_message("assistant", f"echo: {event.message.text}")

        _submit(
            chat,
            "ping",
            [viser.ChatAttachment("doc.pdf", "application/pdf", b"%PDF")],
        )
        assert seen == [(True, ["ping"])]
        assert not chat.busy
        assert [m.text for m in chat.messages] == ["ping", "echo: ping"]
        assert chat.messages[0].attachments[0].name == "doc.pdf"

        # Empty submissions are ignored.
        _submit(chat, "   ")
        assert len(seen) == 1
    finally:
        server.stop()


def test_failing_callback_clears_busy(capsys: pytest.CaptureFixture[str]) -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        chat = server.gui.add_chat()

        @chat.on_submit
        async def _(event: viser.ChatSubmitEvent) -> None:
            raise RuntimeError("boom")

        _submit(chat, "hi")
        assert not chat.busy
        assert "boom" in capsys.readouterr().err
    finally:
        server.stop()


def test_history_actions(tmp_path: Path) -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        chat = server.gui.add_chat(store=viser.ConversationStore(tmp_path))
        chat.add_message("user", "first")
        first_id = chat.conversation.conversation_id

        chat._handle_action("new", "", "")
        assert chat.messages == ()
        chat.add_message("user", "second")
        assert len(chat.conversations) == 2

        chat._handle_action("rename", first_id, "Renamed")
        assert {c.title for c in chat.conversations} == {"Renamed", "second"}

        chat._handle_action("open", first_id, "")
        assert chat.active_conversation_id == first_id
        assert [m.text for m in chat.messages] == ["first"]

        # History is locked while a reply is being generated.
        chat.busy = True
        chat._handle_action("new", "", "")
        assert chat.active_conversation_id == first_id
        chat.busy = False

        chat._handle_action("delete", first_id, "")
        assert chat.active_conversation_id != first_id
        assert [c.title for c in chat.conversations] == ["second"]

        # A new chat on the same store lists the saved conversations.
        other = server.gui.add_chat(store=viser.ConversationStore(tmp_path))
        assert [c.title for c in other.conversations] == ["second"]
    finally:
        server.stop()


def test_panel_parent_raises() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        panel = server.gui.add_panel()
        with pytest.raises(TypeError):
            server.gui.add_chat(parent=panel)  # type: ignore[arg-type]
        chat = server.gui.add_chat(parent=panel.add_tab("Assistant"))
        assert chat.label == "AI Assistant"
    finally:
        server.stop()
