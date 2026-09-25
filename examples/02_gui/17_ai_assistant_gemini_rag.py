"""AI assistant with document search (Gemini File Search)

Chat with a set of PDF documents: they are indexed in a Google Gemini File
Search store, and each question is answered by Gemini using passages retrieved
from them, with the source documents and pages cited below the answer.

Documents can be indexed at startup from a folder (``--docs-dir``), or at any
time by attaching them to a chat message. The store persists on Google's side,
so documents only need to be indexed once.

Requires ``pip install google-genai`` and a ``GEMINI_API_KEY`` environment
variable.

* :meth:`viser.GuiApi.add_chat` for the chat, inside a panel tab
* :meth:`viser.GuiChatHandle.stream` to stream Gemini's answer
"""

from __future__ import annotations

import io
import mimetypes
import time
from pathlib import Path
from typing import Iterator

import tyro

# google-genai needs Python 3.10+, so it isn't installed on 3.9.
from google.genai import Client, errors, types  # pyright: ignore[reportMissingImports]

import viser

SYSTEM_INSTRUCTION = (
    "You are the assistant built into Viser Studio. Answer questions using the "
    "documents available through File Search. If they don't contain the "
    "answer, say so instead of guessing. Answer concisely, in markdown."
)
# File types indexed from --docs-dir. Attachments of other non-image types are
# still sent to File Search, which rejects the ones it can't read.
DOC_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".csv", ".pptx", ".xlsx"}


class GeminiFileSearch:
    """Minimal RAG backend: one File Search store, queried by Gemini."""

    def __init__(
        self,
        store_display_name: str,
        model: str,
        embedding_model: str = "models/gemini-embedding-001",
        client: Client | None = None,
    ) -> None:
        # Client() reads GEMINI_API_KEY (or GOOGLE_API_KEY).
        self.client = Client() if client is None else client
        self.model = model
        self.store_name = self._get_or_create_store(store_display_name, embedding_model)
        self.last_sources: list[str] = []

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
        return list(
            self.client.file_search_stores.documents.list(parent=self.store_name)
        )

    def index(
        self, data: bytes, display_name: str, mime_type: str, timeout: float = 600.0
    ) -> None:
        """Upload a document into the store and wait until it is searchable."""
        operation = self.client.file_search_stores.upload_to_file_search_store(
            file_search_store_name=self.store_name,
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

    def index_folder(self, folder: Path) -> None:
        """Index the documents in a folder that aren't in the store yet."""
        indexed = {d.display_name for d in self.documents()}
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in DOC_SUFFIXES or path.name in indexed:
                continue
            print(f"Indexing {path.name}...")
            self.index(path.read_bytes(), path.name, _guess_mime(path.name))

    def stream_answer(self, conversation: viser.Conversation) -> Iterator[str]:
        """Stream Gemini's answer to the conversation, grounded on the store.

        After the stream ends, ``last_sources`` lists the cited documents."""
        contents: list[types.ContentUnionDict] = [
            types.Content(
                role="user" if m.role == "user" else "model",
                parts=[
                    # Images go inline; documents were indexed into the store.
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
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[
                types.Tool(
                    file_search=types.FileSearch(
                        file_search_store_names=[self.store_name]
                    )
                )
            ],
        )
        sources: dict[str, None] = {}  # Ordered set.
        for chunk in self.client.models.generate_content_stream(
            model=self.model, contents=contents, config=config
        ):
            if chunk.text:
                yield chunk.text
            for candidate in chunk.candidates or ():
                metadata = candidate.grounding_metadata
                for grounding in (metadata.grounding_chunks or ()) if metadata else ():
                    ctx = grounding.retrieved_context
                    if ctx is None or not ctx.title:
                        continue
                    page = f", p. {ctx.page_number}" if ctx.page_number else ""
                    sources[f"{ctx.title}{page}"] = None
        self.last_sources = list(sources)


def _guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def document_list_markdown(rag: GeminiFileSearch) -> str:
    docs = rag.documents()
    if len(docs) == 0:
        return "*No documents yet. Attach a PDF to a chat message to add one.*"
    lines = [f"**{len(docs)} document(s) in the knowledge base:**", ""]
    for d in docs:
        state = "" if d.state == types.DocumentState.STATE_ACTIVE else f" ({d.state})"
        lines.append(f"- {d.display_name}{state}")
    return "\n".join(lines)


def main(
    docs_dir: Path | None = None,
    store: str = "viser-studio-docs",
    model: str = "gemini-3.8-flash",
) -> None:
    """Start the assistant.

    Args:
        docs_dir: Folder of documents to index at startup (already-indexed
            files are skipped).
        store: Display name of the File Search store; created if missing.
        model: Gemini model to answer with. Must support File Search.
    """
    rag = GeminiFileSearch(store, model)
    if docs_dir is not None:
        rag.index_folder(docs_dir)

    server = viser.ViserServer()
    panel = server.gui.add_panel()
    chat = server.gui.add_chat(
        "Document Assistant",
        parent=panel.add_tab("Assistant", viser.Icon.MESSAGE_CHATBOT),
        greeting="Hi there!",
        subtitle="Ask me anything about your documents.",
        suggestions=("Summarize the documents", "What topics do they cover?"),
        disclaimer=(
            "Answers are generated by Gemini from the indexed documents. Always "
            "check the cited sources."
        ),
        placeholder="Ask about your documents…",
        store=viser.ConversationStore(Path.home() / ".viser" / "rag_chat_history"),
    )
    with panel.add_tab("Documents", viser.Icon.FILES):
        doc_list = server.gui.add_markdown(document_list_markdown(rag))

    @chat.on_submit
    def _(event: viser.ChatSubmitEvent) -> None:
        try:
            # Attached documents join the knowledge base before answering.
            for att in event.message.attachments:
                if att.is_image:
                    continue
                event.chat.add_message("system", f"Indexing `{att.name}`…")
                rag.index(att.data, att.name, att.mime_type)
                event.chat.add_message(
                    "system", f"Added `{att.name}` to the documents."
                )
                doc_list.content = document_list_markdown(rag)

            if event.message.text.strip() == "" and not any(
                a.is_image for a in event.message.attachments
            ):
                return  # Only documents were attached; nothing to answer.

            with event.chat.stream() as reply:
                for text in rag.stream_answer(event.conversation):
                    reply.write(text)
                if rag.last_sources:
                    reply.write("\n\n**Sources:** " + "; ".join(rag.last_sources))
        except errors.APIError as e:
            event.chat.add_message("system", f"Gemini API error {e.code}: {e.message}")
        except (RuntimeError, TimeoutError) as e:
            event.chat.add_message("system", str(e))

    print(f"File Search store: {rag.store_name} ({len(rag.documents())} documents)")
    while True:
        time.sleep(10.0)


if __name__ == "__main__":
    tyro.cli(main)
