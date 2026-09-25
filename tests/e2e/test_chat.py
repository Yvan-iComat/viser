"""E2E tests for the AI-assistant chat component."""

from __future__ import annotations

import io
import threading
import time

import numpy as np
from PIL import Image
from playwright.sync_api import Page, expect

import viser


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.full((64, 96, 3), 200, dtype=np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def _click(page: Page, name: str) -> None:
    # dispatch_event: GPU-less runners show a WebGL toast that can intercept
    # real pointer clicks.
    button = page.get_by_role("button", name=name)
    button.first.wait_for(timeout=10_000)
    button.first.dispatch_event("click", event_init={"button": 0})


def test_chat_submit_and_streamed_reply(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """Enter submits; a streamed reply is rendered as markdown."""
    chat = viser_server.gui.add_chat(
        greeting="Hi Tester!", suggestions=("What is my job title?",)
    )
    got: list[str] = []

    @chat.on_submit
    def _(event: viser.ChatSubmitEvent) -> None:
        got.append(event.message.text)
        with event.chat.stream() as reply:
            for part in ("You ", "said ", f"**{event.message.text}**"):
                reply.write(part)

    expect(viser_page.get_by_text("Hi Tester!")).to_be_visible(timeout=10_000)
    box = viser_page.get_by_placeholder("Ask anything…")
    box.fill("hello there")
    box.press("Enter")

    expect(viser_page.locator("strong", has_text="hello there")).to_be_visible(
        timeout=10_000
    )
    assert got == ["hello there"]
    expect(box).to_have_value("")

    # A suggestion chip only shows on an empty conversation; start a new one.
    _click(viser_page, "New conversation")
    _click(viser_page, "What is my job title?")
    expect(
        viser_page.locator("strong", has_text="What is my job title?")
    ).to_be_visible(timeout=10_000)


def test_chat_attachment_upload(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """Attached files reach the server with their bytes and an image thumbnail."""
    chat = viser_server.gui.add_chat()
    received: list[viser.ChatMessage] = []
    done = threading.Event()

    @chat.on_submit
    def _(event: viser.ChatSubmitEvent) -> None:
        received.append(event.message)
        done.set()

    png = _png_bytes()
    viser_page.get_by_placeholder("Ask anything…").wait_for(timeout=10_000)
    viser_page.locator("input[type=file]").set_input_files(
        [
            {"name": "shot.png", "mimeType": "image/png", "buffer": png},
            {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"some notes"},
        ]
    )
    # The text file shows as a named chip; the image as a thumbnail.
    expect(viser_page.get_by_text("notes.txt")).to_be_visible(timeout=5_000)
    expect(viser_page.get_by_alt_text("shot.png")).to_be_visible(timeout=5_000)

    _click(viser_page, "Send")
    assert done.wait(timeout=10.0), "submit callback never fired"

    (message,) = received
    assert message.text == ""
    image, text_file = message.attachments
    assert (image.name, image.mime_type, image.data) == ("shot.png", "image/png", png)
    assert image.thumbnail is not None and image.thumbnail[:2] == b"\xff\xd8"  # JPEG
    assert (text_file.data, text_file.thumbnail) == (b"some notes", None)


def test_chat_history_reopen(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """A previous conversation can be reopened from the history drawer."""
    chat = viser_server.gui.add_chat()

    @chat.on_submit
    def _(event: viser.ChatSubmitEvent) -> None:
        event.chat.add_message("assistant", f"reply to {event.message.text}")

    box = viser_page.get_by_placeholder("Ask anything…")
    box.wait_for(timeout=10_000)
    box.fill("first topic")
    box.press("Enter")
    expect(viser_page.get_by_text("reply to first topic")).to_be_visible(timeout=10_000)

    _click(viser_page, "New conversation")
    expect(viser_page.get_by_text("reply to first topic")).to_be_hidden(timeout=5_000)

    _click(viser_page, "Show conversations")
    expect(viser_page.get_by_text("Conversations", exact=True)).to_be_visible(
        timeout=5_000
    )
    row = viser_page.get_by_text("first topic", exact=True)
    row.dispatch_event("click", event_init={"button": 0})
    expect(viser_page.get_by_text("reply to first topic")).to_be_visible(timeout=5_000)


def test_chat_model_selector(
    viser_server: viser.ViserServer,
    viser_page: Page,
) -> None:
    """The settings button lists the server's models; picking one reaches
    the server, and callbacks see it as ``event.model``."""
    chat = viser_server.gui.add_chat(models=("model-a", "model-b"))
    seen: list[str] = []
    done = threading.Event()

    @chat.on_submit
    def _(event: viser.ChatSubmitEvent) -> None:
        seen.append(event.model)
        done.set()

    _click(viser_page, "Model settings")
    select = viser_page.get_by_role("combobox", name="AI model")
    expect(select).to_have_value("model-a", timeout=5_000)
    select.dispatch_event("click")
    viser_page.get_by_role("option", name="model-b").dispatch_event("click")

    deadline = time.time() + 5.0
    while chat.model != "model-b" and time.time() < deadline:
        time.sleep(0.05)
    assert chat.model == "model-b"

    box = viser_page.get_by_placeholder("Ask anything…")
    box.fill("hi")
    box.press("Enter")
    assert done.wait(timeout=10.0)
    assert seen == ["model-b"]

    # Server-side changes to the list show up in the dropdown.
    chat.models = ("model-c",)
    _click(viser_page, "Model settings")
    expect(viser_page.get_by_role("combobox", name="AI model")).to_have_value(
        "model-c", timeout=5_000
    )
