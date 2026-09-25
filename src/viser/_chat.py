"""Conversation model and persistence for the chat GUI component."""

from __future__ import annotations

import base64
import dataclasses
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

ChatRole = Literal["user", "assistant", "system"]


def _make_id() -> str:
    return uuid.uuid4().hex


@dataclasses.dataclass
class ChatAttachment:
    """A file attached to a chat message."""

    name: str
    """File name."""
    mime_type: str
    """MIME type, e.g. ``"image/png"`` or ``"application/pdf"``."""
    data: bytes
    """Raw file contents."""
    thumbnail: bytes | None = None
    """Small JPEG preview for images, used for display only."""

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")


@dataclasses.dataclass
class ChatMessage:
    """A single message in a conversation."""

    role: ChatRole
    text: str
    attachments: list[ChatAttachment] = dataclasses.field(default_factory=list)
    message_id: str = dataclasses.field(default_factory=_make_id)
    timestamp: float = dataclasses.field(default_factory=time.time)


@dataclasses.dataclass
class Conversation:
    """An ordered list of chat messages, with a title."""

    conversation_id: str = dataclasses.field(default_factory=_make_id)
    title: str = "New conversation"
    messages: list[ChatMessage] = dataclasses.field(default_factory=list)
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)

    def append(self, message: ChatMessage) -> None:
        """Append a message, deriving the title from the first user message."""
        if (
            message.role == "user"
            and message.text.strip() != ""
            and not any(m.role == "user" for m in self.messages)
        ):
            text = " ".join(message.text.split())
            self.title = text if len(text) <= 48 else text[:47] + "…"
        self.messages.append(message)
        self.updated_at = time.time()


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ConversationStore:
    """Saves conversations as JSON files in a directory.

    Each conversation is stored as ``<directory>/<conversation_id>.json``;
    attachment bytes live next to it in ``<directory>/<conversation_id>/``.
    Pass ``directory=None`` to keep conversations in memory only.
    """

    def __init__(self, directory: str | Path | None = "chat_history") -> None:
        self._directory = None if directory is None else Path(directory)
        self._memory: dict[str, Conversation] = {}
        self._lock = threading.Lock()
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path | None:
        return self._directory

    def list_conversations(self) -> list[Conversation]:
        """All saved conversations, most recently updated first.

        Only titles and timestamps are guaranteed to be populated; call
        :meth:`load` to get the full message list.
        """
        with self._lock:
            if self._directory is None:
                conversations = list(self._memory.values())
            else:
                conversations = []
                for path in self._directory.glob("*.json"):
                    try:
                        conversations.append(self._read(path, with_files=False))
                    except (OSError, ValueError, KeyError):
                        continue  # Skip unreadable files rather than failing.
        return sorted(conversations, key=lambda c: c.updated_at, reverse=True)

    def load(self, conversation_id: str) -> Conversation | None:
        """Load a conversation, or return None if it doesn't exist."""
        with self._lock:
            if self._directory is None:
                return self._memory.get(conversation_id)
            path = self._json_path(conversation_id)
            if not path.exists():
                return None
            return self._read(path, with_files=True)

    def save(self, conversation: Conversation) -> None:
        """Create or overwrite a conversation."""
        with self._lock:
            if self._directory is None:
                self._memory[conversation.conversation_id] = conversation
                return
            files_dir = self._directory / conversation.conversation_id
            messages = []
            for message in conversation.messages:
                attachments = []
                for i, att in enumerate(message.attachments):
                    file_name = (
                        f"{message.message_id}_{i}_{_SAFE_NAME.sub('_', att.name)}"
                    )
                    file_path = files_dir / file_name
                    if not file_path.exists():
                        files_dir.mkdir(parents=True, exist_ok=True)
                        file_path.write_bytes(att.data)
                    attachments.append(
                        {
                            "name": att.name,
                            "mime_type": att.mime_type,
                            "file": file_name,
                            "thumbnail": None
                            if att.thumbnail is None
                            else base64.b64encode(att.thumbnail).decode("ascii"),
                        }
                    )
                messages.append(
                    {
                        "message_id": message.message_id,
                        "role": message.role,
                        "text": message.text,
                        "timestamp": message.timestamp,
                        "attachments": attachments,
                    }
                )
            payload = {
                "conversation_id": conversation.conversation_id,
                "title": conversation.title,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "messages": messages,
            }
            # Write-then-rename so a crash never leaves a truncated file.
            path = self._json_path(conversation.conversation_id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
            tmp.replace(path)

    def delete(self, conversation_id: str) -> None:
        """Delete a conversation and its attachments. Unknown ids are ignored."""
        with self._lock:
            if self._directory is None:
                self._memory.pop(conversation_id, None)
                return
            self._json_path(conversation_id).unlink(missing_ok=True)
            files_dir = self._directory / conversation_id
            if files_dir.is_dir():
                for f in files_dir.iterdir():
                    f.unlink()
                files_dir.rmdir()

    def _json_path(self, conversation_id: str) -> Path:
        assert self._directory is not None
        if _SAFE_NAME.search(conversation_id) or conversation_id in ("", ".", ".."):
            raise ValueError(f"Invalid conversation id: {conversation_id!r}")
        return self._directory / f"{conversation_id}.json"

    def _read(self, path: Path, with_files: bool) -> Conversation:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        files_dir = path.with_suffix("")
        messages = []
        for m in raw["messages"] if with_files else ():
            attachments = []
            for a in m.get("attachments", ()):
                file_path = files_dir / a["file"]
                attachments.append(
                    ChatAttachment(
                        name=a["name"],
                        mime_type=a["mime_type"],
                        data=file_path.read_bytes() if file_path.exists() else b"",
                        thumbnail=None
                        if a.get("thumbnail") is None
                        else base64.b64decode(a["thumbnail"]),
                    )
                )
            messages.append(
                ChatMessage(
                    role=m["role"],
                    text=m["text"],
                    attachments=attachments,
                    message_id=m["message_id"],
                    timestamp=m["timestamp"],
                )
            )
        return Conversation(
            conversation_id=raw["conversation_id"],
            title=raw["title"],
            messages=messages,
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
        )
