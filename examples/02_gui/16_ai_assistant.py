"""AI assistant chat

Add an AI-assistant chat panel: a message list, a text box that accepts pasted
or dropped images and documents, and a history of saved conversations.

The chat only transports messages; replies come from your own callback. This
example streams answers from Claude when the ``anthropic`` package is installed
and ``ANTHROPIC_API_KEY`` is set, and falls back to a local echo bot otherwise.

* :meth:`viser.GuiApi.add_chat` to create the chat inside a panel tab
* :meth:`viser.GuiChatHandle.on_submit` to answer submitted messages
* :meth:`viser.GuiChatHandle.stream` to stream a reply token by token
* :class:`viser.ConversationStore` to save conversations as JSON files
"""

from __future__ import annotations

import base64
import importlib.util
import os
import time
from pathlib import Path

import viser

SYSTEM_PROMPT = (
    "You are the assistant built into Viser Studio, a 3D viewer used for "
    "robotics and CAM work. Answer concisely, using markdown when it helps."
)
CLAUDE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
TEXT_TYPES = {"application/json", "application/xml", "text/csv", "text/markdown"}


def to_claude_content(message: viser.ChatMessage) -> list[dict]:
    """Convert a chat message into Claude content blocks.

    Images and PDFs are sent as base64 blocks, text files as plain-text
    documents; other file types are mentioned by name only.
    """
    blocks: list[dict] = []
    notes: list[str] = []
    for att in message.attachments:
        if att.mime_type in CLAUDE_IMAGE_TYPES:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": att.mime_type,
                        "data": base64.b64encode(att.data).decode("ascii"),
                    },
                }
            )
        elif att.mime_type == "application/pdf":
            blocks.append(
                {
                    "type": "document",
                    "title": att.name,
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(att.data).decode("ascii"),
                    },
                }
            )
        elif att.mime_type.startswith("text/") or att.mime_type in TEXT_TYPES:
            blocks.append(
                {
                    "type": "document",
                    "title": att.name,
                    "source": {
                        "type": "text",
                        "media_type": "text/plain",
                        "data": att.data.decode("utf-8", errors="replace"),
                    },
                }
            )
        else:
            notes.append(f"[Attached file {att.name!r} ({att.mime_type}) not readable]")
    text = "\n".join([*notes, message.text]).strip()
    if text != "":
        blocks.append({"type": "text", "text": text})
    return blocks


def claude_reply(event: viser.ChatSubmitEvent) -> None:
    import anthropic  # pyright: ignore[reportMissingImports] (not on Python 3.9)

    client = anthropic.Anthropic()
    history = [
        {"role": m.role, "content": to_claude_content(m)}
        for m in event.conversation.messages
        if m.role in ("user", "assistant")
    ]
    try:
        with event.chat.stream() as reply:
            # `fallbacks="default"` re-runs a request declined by the safety
            # classifiers on Anthropic's recommended fallback model.
            with client.beta.messages.stream(
                model="claude-opus-5",
                max_tokens=64000,
                system=SYSTEM_PROMPT,
                messages=history,  # type: ignore[arg-type]
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for text in stream.text_stream:
                    reply.write(text)
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                reply.write("\n\n*The model declined to answer this request.*")
            elif final.stop_reason == "max_tokens":
                reply.write("\n\n*(Reply truncated: output limit reached.)*")
    except anthropic.AuthenticationError:
        event.chat.add_message(
            "system", "Authentication failed: check ANTHROPIC_API_KEY."
        )
    except anthropic.RateLimitError:
        event.chat.add_message("system", "Rate limited, please retry in a moment.")
    except anthropic.APIStatusError as e:
        event.chat.add_message("system", f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        event.chat.add_message("system", "Could not reach the Anthropic API.")


def echo_reply(event: viser.ChatSubmitEvent) -> None:
    """Offline stand-in for an LLM: streams back a description of the message."""
    lines = [f"You said: **{event.message.text}**"] if event.message.text else []
    for att in event.message.attachments:
        lines.append(
            f"- attachment `{att.name}` ({att.mime_type}, {len(att.data)} bytes)"
        )
    lines.append(
        "\n*Echo mode: install `anthropic` and set `ANTHROPIC_API_KEY` for real answers.*"
    )
    with event.chat.stream() as reply:
        for word in "\n".join(lines).split(" "):
            reply.write(word + " ")
            time.sleep(0.03)


def main() -> None:
    server = viser.ViserServer()
    server.scene.add_grid("/grid", width=4.0, height=4.0)

    use_claude = (
        importlib.util.find_spec("anthropic") is not None
        and "ANTHROPIC_API_KEY" in os.environ
    )

    panel = server.gui.add_panel()
    chat = server.gui.add_chat(
        "AI Assistant",
        parent=panel.add_tab("Assistant", viser.Icon.MESSAGE_CHATBOT),
        greeting="Hi there!",
        subtitle="What are we tackling today?",
        suggestions=(
            "How do I load a URDF?",
            "Explain OPW kinematics",
            "Summarize the attached document",
        ),
        disclaimer=(
            "This is an AI assistant, not a human. Always check for accuracy. "
            "Conversations are saved on the server."
        ),
        # Saved outside the repo so conversations are never committed.
        store=viser.ConversationStore(Path.home() / ".viser" / "chat_history"),
    )
    chat.on_submit(claude_reply if use_claude else echo_reply)
    print(f"Chat backend: {'Claude' if use_claude else 'echo'}")

    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    main()
