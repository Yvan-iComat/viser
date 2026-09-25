"""Document question answering (RAG) for the chat component, backed by
Google Gemini File Search."""

from __future__ import annotations

import io
import mimetypes
import time
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Sequence

import viser

if TYPE_CHECKING:
    # google-genai needs Python 3.10+, so it isn't installed on 3.9.
    from google.genai import Client, types  # pyright: ignore[reportMissingImports]

DEFAULT_SYSTEM_INSTRUCTION = (
    "You are the assistant built into Viser Studio. Answer questions using the "
    "documents available through File Search. If they don't contain the "
    "answer, say so instead of guessing. Answer concisely, in markdown."
)
DEFAULT_DOC_SUFFIXES = (".pdf", ".docx", ".txt", ".md", ".csv", ".pptx", ".xlsx")


def _default_client() -> Client:
    """``google.genai.Client()``, which reads GEMINI_API_KEY / GOOGLE_API_KEY."""
    try:
        from google.genai import Client  # pyright: ignore[reportMissingImports]
    except ImportError as e:
        raise ImportError(
            "GeminiFileSearch requires google-genai: `pip install google-genai`."
        ) from e
    return Client()


def _guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class GeminiFileSearch:
    """Answer chat questions from a set of documents, using a Gemini File
    Search store for retrieval.

    Documents are indexed into a store on Google's side (created on first use,
    then reused by display name), so each only needs to be indexed once. Each
    question is answered by Gemini with the ``file_search`` tool, and the cited
    documents and pages are appended to the answer.

    Requires ``pip install google-genai`` and a ``GEMINI_API_KEY`` (or
    ``GOOGLE_API_KEY``) environment variable, unless ``client`` is given.

    Example:
        >>> from viser.extras import GeminiFileSearch
        >>>
        >>> rag = GeminiFileSearch("my-docs")
        >>> rag.index_folder(Path("manuals"))
        >>> chat = server.gui.add_chat("Document Assistant")
        >>> rag.connect(chat)

    Args:
        store: Display name of the File Search store; created if missing.
        model: Gemini model used to answer. Must support File Search.
        embedding_model: Embedding model for a newly created store.
        system_instruction: System prompt for answers.
        client: Optional preconfigured ``google.genai.Client``.
    """

    def __init__(
        self,
        store: str = "viser-studio-docs",
        *,
        model: str = "gemini-3.8-flash",
        embedding_model: str = "models/gemini-embedding-001",
        system_instruction: str = DEFAULT_SYSTEM_INSTRUCTION,
        client: Client | None = None,
    ) -> None:
        self.client: Client = _default_client() if client is None else client
        self.model = model
        self.system_instruction = system_instruction
        self._store_name = self._get_or_create_store(store, embedding_model)

    @property
    def store_name(self) -> str:
        """Resource name of the File Search store, e.g. ``fileSearchStores/abc``."""
        return self._store_name

    def _get_or_create_store(self, display_name: str, embedding_model: str) -> str:
        for store in self.client.file_search_stores.list():
            if store.display_name == display_name and store.name is not None:
                return store.name
        store = self.client.file_search_stores.create(
            config={"display_name": display_name, "embedding_model": embedding_model}
        )
        assert store.name is not None
        return store.name

    def documents(self) -> list[types.Document]:
        """Documents currently in the store."""
        return list(
            self.client.file_search_stores.documents.list(parent=self._store_name)
        )

    def index(
        self,
        data: bytes | str | Path,
        display_name: str | None = None,
        mime_type: str | None = None,
        timeout: float = 600.0,
    ) -> None:
        """Upload a document into the store and wait until it is searchable.

        Args:
            data: File contents, or a path to the file.
            display_name: Name shown in citations. Defaults to the file name.
            mime_type: MIME type. Guessed from the name if omitted.
            timeout: Seconds to wait for indexing to finish.
        """
        if not isinstance(data, bytes):
            path = Path(data)
            display_name = path.name if display_name is None else display_name
            data = path.read_bytes()
        if display_name is None:
            raise ValueError("`display_name` is required when passing bytes.")
        mime_type = _guess_mime(display_name) if mime_type is None else mime_type

        operation = self.client.file_search_stores.upload_to_file_search_store(
            file_search_store_name=self._store_name,
            file=io.BytesIO(data),
            config={"display_name": display_name, "mime_type": mime_type},
        )
        deadline = time.time() + timeout
        while not operation.done:
            if time.time() > deadline:
                raise TimeoutError(f"Indexing {display_name!r} timed out.")
            time.sleep(2.0)
            operation = self.client.operations.get(operation)
        if operation.error:
            raise RuntimeError(f"Indexing {display_name!r} failed: {operation.error}")

    def index_folder(
        self,
        folder: str | Path,
        suffixes: Sequence[str] = DEFAULT_DOC_SUFFIXES,
    ) -> list[str]:
        """Index the documents in a folder that aren't in the store yet.

        Files are matched to indexed documents by name.

        Returns:
            Names of the newly indexed files.
        """
        indexed = {d.display_name for d in self.documents()}
        added = []
        for path in sorted(Path(folder).iterdir()):
            if path.suffix.lower() not in suffixes or path.name in indexed:
                continue
            self.index(path)
            added.append(path.name)
        return added

    def stream_answer(
        self,
        conversation: viser.Conversation,
        sources: list[str] | None = None,
    ) -> Iterator[str]:
        """Stream Gemini's answer to the last message of a conversation.

        The whole conversation is sent as context. Image attachments are sent
        inline; other attachments are expected to have been indexed.

        Args:
            conversation: Conversation to answer.
            sources: Optional list, filled with the cited documents (and
                pages) once the stream ends.
        """
        from google.genai import types  # pyright: ignore[reportMissingImports]

        contents: list[types.ContentUnionDict] = [
            types.Content(
                role="user" if m.role == "user" else "model",
                parts=[
                    *[
                        types.Part.from_bytes(data=a.data, mime_type=a.mime_type)
                        for a in m.attachments
                        if a.is_image
                    ],
                    types.Part(text=m.text or "(see attachment)"),
                ],
            )
            for m in conversation.messages
            if m.role in ("user", "assistant")
        ]
        config = types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            tools=[
                types.Tool(
                    file_search=types.FileSearch(
                        file_search_store_names=[self._store_name]
                    )
                )
            ],
        )
        cited: dict[str, None] = {}  # Ordered set.
        for chunk in self.client.models.generate_content_stream(
            model=self.model, contents=contents, config=config
        ):
            if chunk.text:
                yield chunk.text
            for candidate in chunk.candidates or ():
                metadata = candidate.grounding_metadata
                if metadata is None:
                    continue
                for grounding in metadata.grounding_chunks or ():
                    ctx = grounding.retrieved_context
                    if ctx is None or not ctx.title:
                        continue
                    page = f", p. {ctx.page_number}" if ctx.page_number else ""
                    cited[f"{ctx.title}{page}"] = None
        if sources is not None:
            sources.extend(cited)

    def documents_markdown(self) -> str:
        """Markdown list of the documents in the store, for display."""
        from google.genai import types  # pyright: ignore[reportMissingImports]

        docs = self.documents()
        if len(docs) == 0:
            return "*No documents yet. Attach a PDF to a chat message to add one.*"
        lines = [f"**{len(docs)} document(s) in the knowledge base:**", ""]
        for d in docs:
            active = d.state == types.DocumentState.STATE_ACTIVE
            lines.append(f"- {d.display_name}" + ("" if active else f" ({d.state})"))
        return "\n".join(lines)

    def answer(
        self,
        event: viser.ChatSubmitEvent,
        document_list: viser.GuiMarkdownHandle | None = None,
    ) -> None:
        """Handle a chat submission: index attached documents, then stream
        the answer with its sources. Errors are reported in the chat.

        Usually registered through :meth:`connect`.
        """
        from google.genai import errors  # pyright: ignore[reportMissingImports]

        chat = event.chat
        try:
            for att in event.message.attachments:
                if att.is_image:
                    continue
                chat.add_message("system", f"Indexing `{att.name}`…")
                self.index(att.data, att.name, att.mime_type)
                chat.add_message("system", f"Added `{att.name}` to the documents.")
                if document_list is not None:
                    document_list.content = self.documents_markdown()

            if event.message.text.strip() == "" and not any(
                a.is_image for a in event.message.attachments
            ):
                return  # Only documents were attached; nothing to answer.

            sources: list[str] = []
            with chat.stream() as reply:
                for text in self.stream_answer(event.conversation, sources):
                    reply.write(text)
                if sources:
                    reply.write("\n\n**Sources:** " + "; ".join(sources))
        except errors.APIError as e:
            chat.add_message("system", f"Gemini API error {e.code}: {e.message}")
        except (RuntimeError, TimeoutError) as e:
            chat.add_message("system", str(e))

    def connect(
        self,
        chat: viser.GuiChatHandle,
        document_list: viser.GuiMarkdownHandle | None = None,
    ) -> None:
        """Answer every message submitted to ``chat`` from the documents.

        Args:
            chat: Chat to answer.
            document_list: Optional markdown element kept updated with the
                list of indexed documents.
        """
        if document_list is not None:
            document_list.content = self.documents_markdown()
        chat.on_submit(lambda event: self.answer(event, document_list))
