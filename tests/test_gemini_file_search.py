"""Unit tests for viser.extras.GeminiFileSearch, against a fake genai client."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

import viser
from viser.infra import ClientId

types = pytest.importorskip("google.genai.types")

from viser.extras import GeminiFileSearch  # noqa: E402

STORE = "fileSearchStores/fake-123"


class _Op:
    def __init__(self, error: Any = None) -> None:
        self.done = False
        self.error = error


class _Documents:
    def __init__(self) -> None:
        self.docs: list[Any] = []

    def list(self, *, parent: str) -> list[Any]:
        assert parent == STORE
        return list(self.docs)


class _Stores:
    def __init__(self, existing: list[Any]) -> None:
        self.existing = existing
        self.documents = _Documents()
        self.created: list[dict] = []
        self.uploads: list[tuple[bytes, dict]] = []
        self.fail_uploads = False

    def list(self) -> list[Any]:
        return self.existing

    def create(self, *, config: dict) -> Any:
        self.created.append(config)
        return types.FileSearchStore(
            name=STORE,
            display_name=config["display_name"],
            embedding_model=config["embedding_model"],
        )

    def upload_to_file_search_store(
        self, *, file_search_store_name: str, file: Any, config: dict
    ) -> _Op:
        assert file_search_store_name == STORE
        self.uploads.append((file.read(), dict(config)))
        self.documents.docs.append(
            types.Document(
                display_name=config["display_name"],
                state=types.DocumentState.STATE_ACTIVE,
            )
        )
        return _Op(error="bad file" if self.fail_uploads else None)


class _Operations:
    def get(self, op: _Op) -> _Op:
        op.done = True
        return op


def _response(text: str, titles: tuple[tuple[str, int | None], ...] = ()) -> Any:
    chunks = [
        types.GroundingChunk(
            retrieved_context=types.GroundingChunkRetrievedContext(
                title=title, page_number=page
            )
        )
        for title, page in titles
    ]
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                grounding_metadata=types.GroundingMetadata(grounding_chunks=chunks)
                if chunks
                else None,
            )
        ]
    )


class _Models:
    def __init__(self) -> None:
        self.requests: list[tuple[str, Any, Any]] = []

    def generate_content_stream(self, *, model: str, contents: Any, config: Any):
        self.requests.append((model, contents, config))
        yield _response("The limit is ")
        yield _response(
            "**120 Nm**.", (("manual.pdf", 7), ("manual.pdf", 7), ("spec.pdf", None))
        )


class _Client:
    def __init__(self, existing: list[Any] | None = None) -> None:
        self.file_search_stores = _Stores(existing or [])
        self.operations = _Operations()
        self.models = _Models()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("viser.extras._gemini_file_search.time.sleep", lambda _: None)


def _rag(client: _Client) -> GeminiFileSearch:
    return GeminiFileSearch("docs", model="m", client=client)  # type: ignore[arg-type]


def test_store_is_reused_or_created() -> None:
    client = _Client()
    assert _rag(client).store_name == STORE
    assert client.file_search_stores.created[0]["display_name"] == "docs"

    existing = _Client([types.FileSearchStore(name=STORE, display_name="docs")])
    _rag(existing)
    assert existing.file_search_stores.created == []


def test_embedding_model() -> None:
    # New stores default to the multimodal Gemini Embedding 2.
    client = _Client()
    rag = _rag(client)
    assert client.file_search_stores.created[0]["embedding_model"] == (
        "models/gemini-embedding-2"
    )
    assert rag.embedding_model == "models/gemini-embedding-2"
    assert "gemini-embedding-2" in rag.documents_markdown()

    # An existing store keeps its model; asking for another one warns.
    old = types.FileSearchStore(
        name=STORE, display_name="docs", embedding_model="models/gemini-embedding-001"
    )
    with pytest.warns(UserWarning, match="can't be changed"):
        rag = _rag(_Client([old]))
    assert rag.embedding_model == "models/gemini-embedding-001"

    # Matching models (with or without the "models/" prefix) don't warn.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        GeminiFileSearch(
            "docs",
            embedding_model="gemini-embedding-001",
            client=_Client([old]),  # type: ignore[arg-type]
        )


def test_index_folder_skips_indexed_and_unsupported(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"%PDF a")
    (tmp_path / "b.pdf").write_bytes(b"%PDF b")
    (tmp_path / "image.png").write_bytes(b"png")
    client = _Client()
    rag = _rag(client)

    assert rag.index_folder(tmp_path) == ["a.pdf", "b.pdf"]
    assert client.file_search_stores.uploads[0] == (
        b"%PDF a",
        {"display_name": "a.pdf", "mime_type": "application/pdf"},
    )
    # Second run: everything is already in the store.
    assert rag.index_folder(tmp_path) == []
    assert "a.pdf" in rag.documents_markdown()


def test_index_failure_raises() -> None:
    client = _Client()
    client.file_search_stores.fail_uploads = True
    with pytest.raises(RuntimeError, match="bad file"):
        _rag(client).index(b"x", "x.pdf")
    with pytest.raises(ValueError):
        _rag(client).index(b"x")


def test_stream_answer_history_and_sources() -> None:
    client = _Client()
    rag = _rag(client)
    conv = viser.Conversation()
    conv.append(viser.ChatMessage("user", "first question"))
    conv.append(viser.ChatMessage("assistant", "first answer"))
    conv.append(viser.ChatMessage("system", "not sent"))
    conv.append(
        viser.ChatMessage(
            "user",
            "what is the limit?",
            [viser.ChatAttachment("photo.png", "image/png", b"PNG")],
        )
    )

    sources: list[str] = []
    assert "".join(rag.stream_answer(conv, sources)) == "The limit is **120 Nm**."
    assert sources == ["manual.pdf, p. 7", "spec.pdf"]

    model, contents, config = client.models.requests[0]
    assert model == "m"
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[2].parts[0].inline_data.mime_type == "image/png"
    assert config.tools[0].file_search.file_search_store_names == [STORE]


def test_connect_indexes_attachments_and_answers() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        client = _Client()
        rag = _rag(client)
        chat = server.gui.add_chat(models=("m", "m-pro"))
        chat.model = "m-pro"
        doc_list = server.gui.add_markdown("")
        rag.connect(chat, document_list=doc_list)
        assert "No documents" in doc_list.content

        async def submit() -> None:
            chat._handle_submit(
                None,  # type: ignore[arg-type]
                ClientId(0),
                "what is the limit?",
                [viser.ChatAttachment("manual.pdf", "application/pdf", b"%PDF")],
            )
            await asyncio.gather(*chat._tasks)

        thread = threading.Thread(target=lambda: asyncio.run(submit()))
        thread.start()
        thread.join()

        assert client.file_search_stores.uploads[0][0] == b"%PDF"
        # The model selected in the chat is used, not the instance default.
        assert client.models.requests[0][0] == "m-pro"
        assert "manual.pdf" in doc_list.content
        texts = [(m.role, m.text) for m in chat.messages]
        assert texts[1:3] == [
            ("system", "Indexing `manual.pdf`…"),
            ("system", "Added `manual.pdf` to the documents."),
        ]
        assert texts[3][0] == "assistant"
        assert texts[3][1].startswith("The limit is **120 Nm**.")
        assert "**Sources:** manual.pdf, p. 7; spec.pdf" in texts[3][1]
    finally:
        server.stop()


def test_connect_documents_tab() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        # Default: a "Documents" tab is added next to the chat's tab.
        panel = server.gui.add_panel()
        chat = server.gui.add_chat(parent=panel.add_tab("Assistant"))
        doc_list = _rag(_Client()).connect(chat)
        assert [t._label for t in panel._tab_handles] == ["Assistant", "Documents"]
        assert doc_list is not None and "No documents" in doc_list.content

        # Hidden on request.
        panel2 = server.gui.add_panel()
        chat2 = server.gui.add_chat(parent=panel2.add_tab("Assistant"))
        assert _rag(_Client()).connect(chat2, show_documents=False) is None
        assert [t._label for t in panel2._tab_handles] == ["Assistant"]

        # A chat outside any tab can't get one: warn instead.
        chat3 = server.gui.add_chat()
        with pytest.warns(UserWarning, match="needs the chat to be in a tab"):
            assert _rag(_Client()).connect(chat3) is None
    finally:
        server.stop()
