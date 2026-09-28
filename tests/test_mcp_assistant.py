"""Unit tests for viser.extras.McpAssistant, against a fake backend and MCP server."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Sequence

import pytest

import viser
from viser.extras import LlmBackend, McpAssistant, ModelTurn, ToolCall, ToolResult
from viser.infra import ClientId

ENDPOINT = "http://127.0.0.1:9999/mcp"


class _Content:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _ToolResponse:
    def __init__(self, text: str, is_error: bool = False) -> None:
        self.content = [_Content(text)]
        self.is_error = is_error


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"does {name}"
        self.input_schema: dict[str, Any] = {"type": "object"}


class _ToolList:
    def __init__(self, tools: list[_Tool]) -> None:
        self.tools = tools


class _McpClient:
    """Stands in for `mcp.Client`, recording the calls made against it."""

    instances: list[_McpClient] = []

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.calls: list[tuple[str, dict]] = []
        self.results: dict[str, _ToolResponse] = {}
        self.entered = False
        self.exited = False
        _McpClient.instances.append(self)

    async def __aenter__(self) -> _McpClient:
        self.entered = True
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.exited = True
        return False

    async def list_tools(self) -> _ToolList:
        return _ToolList([_Tool("add_box"), _Tool("get_scene_state")])

    async def call_tool(self, name: str, arguments: dict) -> _ToolResponse:
        self.calls.append((name, dict(arguments)))
        return self.results.get(name, _ToolResponse('{"success": true}'))


class _Backend:
    """Backend that replays a scripted list of turns."""

    def __init__(self, turns: Sequence[ModelTurn], error_message: Any = None) -> None:
        self.turns = list(turns)
        self.error_message = error_message
        self.seen: list[tuple[Any, str, list[str]]] = []
        self.histories: list[Any] = []

    def start_history(self, conversation: viser.Conversation) -> list[str]:
        return [f"{m.role}:{m.text}" for m in conversation.messages if m.role != "system"]

    def extend_history(
        self, history: list[str], turn: ModelTurn, results: Sequence[ToolResult]
    ) -> list[str]:
        self.histories.append(list(history))
        return [
            *history,
            f"model:{turn.state}",
            *[f"tool:{r.call.name}:{r.text}:{r.is_error}" for r in results],
        ]

    async def stream_turn(
        self, history: Any, model: str, tools: Sequence[Any], write: Any
    ) -> ModelTurn:
        self.seen.append((list(history), model, [t.name for t in tools]))
        turn = self.turns.pop(0)
        write(turn.text)
        return turn

    def format_error(self, error: BaseException) -> Any:
        return self.error_message


@pytest.fixture(autouse=True)
def _fake_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `from mcp import Client` inside answer() resolve to the fake."""
    import sys
    import types as pytypes

    _McpClient.instances.clear()
    module = pytypes.ModuleType("mcp")
    module.Client = _McpClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp", module)


def _run(chat: viser.GuiChatHandle, text: str = "hello") -> None:
    """Submit a message and wait for the assistant's task to finish."""

    async def submit() -> None:
        chat._handle_submit(None, ClientId(0), text, [])  # type: ignore[arg-type]
        await asyncio.gather(*chat._tasks)

    thread = threading.Thread(target=lambda: asyncio.run(submit()))
    thread.start()
    thread.join()


def test_protocol_is_satisfied_by_backends() -> None:
    # The shipped backends structurally satisfy LlmBackend, as does a fake.
    assert isinstance(_Backend([]), LlmBackend)


def test_plain_answer_without_tools() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        backend = _Backend([ModelTurn(text="Nothing to do.")])
        chat = server.gui.add_chat()
        McpAssistant(ENDPOINT, backend, model="m").connect(chat)
        _run(chat)

        assert [m.text for m in chat.messages if m.role == "assistant"] == [
            "Nothing to do."
        ]
        # The session is opened and closed even when no tool is called.
        client = _McpClient.instances[0]
        assert (client.endpoint, client.entered, client.exited) == (ENDPOINT, True, True)
        assert client.calls == []
    finally:
        server.stop()


def test_tool_calls_are_forwarded_and_fed_back() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        backend = _Backend(
            [
                ModelTurn(
                    text="Adding.",
                    tool_calls=[ToolCall("add_box", {"name": "b"}, "call-1")],
                    state="turn-1",
                ),
                ModelTurn(text="Done."),
            ]
        )
        chat = server.gui.add_chat()
        McpAssistant(ENDPOINT, backend, model="m").connect(chat)
        _run(chat)

        client = _McpClient.instances[0]
        assert client.calls == [("add_box", {"name": "b"})]

        # The tool result is fed back into the next turn's history.
        assert backend.seen[1][0][-1] == 'tool:add_box:{"success": true}:False'
        # Both turns' text and the tool call marker reach the chat.
        reply = [m.text for m in chat.messages if m.role == "assistant"][0]
        assert reply.startswith("Adding.")
        assert '> `add_box({"name": "b"})` — ok' in reply
        assert reply.endswith("Done.")
    finally:
        server.stop()


def test_failed_tool_is_marked() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        backend = _Backend(
            [
                ModelTurn(tool_calls=[ToolCall("add_box", {}, "c1")], state="t"),
                ModelTurn(tool_calls=[ToolCall("add_box", {}, "c2")], state="t"),
                ModelTurn(text="Gave up."),
            ]
        )
        chat = server.gui.add_chat()
        McpAssistant(ENDPOINT, backend, model="m").connect(chat)

        def make(endpoint: str) -> _McpClient:
            client = _McpClient(endpoint)
            # Transport-level failure, then a JSON payload reporting failure.
            client.results["add_box"] = _ToolResponse("boom", is_error=True)
            return client

        import mcp  # type: ignore[import-not-found]

        mcp.Client = make  # type: ignore[attr-defined]
        _run(chat)

        reply = [m.text for m in chat.messages if m.role == "assistant"][0]
        assert "— failed" in reply
        # Failures are reported to the model rather than aborting the loop.
        assert backend.seen[1][0][-1] == "tool:add_box:boom:True"
    finally:
        server.stop()


def test_success_false_payload_counts_as_failure() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        backend = _Backend(
            [
                ModelTurn(tool_calls=[ToolCall("add_box", {}, "c1")], state="t"),
                ModelTurn(text="ok"),
            ]
        )
        chat = server.gui.add_chat()
        McpAssistant(ENDPOINT, backend, model="m").connect(chat)

        def make(endpoint: str) -> _McpClient:
            client = _McpClient(endpoint)
            client.results["add_box"] = _ToolResponse('{"success": false}')
            return client

        import mcp  # type: ignore[import-not-found]

        mcp.Client = make  # type: ignore[attr-defined]
        _run(chat)

        assert "— failed" in [m.text for m in chat.messages if m.role == "assistant"][0]
    finally:
        server.stop()


def test_tool_rounds_are_capped() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        # Always asks for another tool call: the cap must stop the loop.
        backend = _Backend(
            [ModelTurn(tool_calls=[ToolCall("add_box", {}, "c")], state="t")] * 5
        )
        chat = server.gui.add_chat()
        McpAssistant(ENDPOINT, backend, model="m", max_tool_rounds=3).connect(chat)
        _run(chat)

        assert len(_McpClient.instances[0].calls) == 3
        assert "Stopped after 3 rounds" in [
            m.text for m in chat.messages if m.role == "assistant"
        ][0]
    finally:
        server.stop()


def test_chat_model_overrides_default() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:
        backend = _Backend([ModelTurn(text="hi")])
        chat = server.gui.add_chat(models=("m", "m-pro"))
        chat.model = "m-pro"
        McpAssistant(ENDPOINT, backend, model="m").connect(chat)
        _run(chat)

        assert backend.seen[0][1] == "m-pro"
    finally:
        server.stop()


def test_backend_formats_its_own_errors() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:

        class _Boom(_Backend):
            async def stream_turn(self, *args: Any, **kwargs: Any) -> ModelTurn:
                raise RuntimeError("quota")

        chat = server.gui.add_chat()
        backend = _Boom([], error_message="API error 429: quota")
        McpAssistant(ENDPOINT, backend, model="m").connect(chat)
        _run(chat)

        assert [m.text for m in chat.messages if m.role == "system"] == [
            "API error 429: quota"
        ]
    finally:
        server.stop()


def test_gemini_backend_round_trips_a_tool_call() -> None:
    """The shipped Gemini adapter, against a fake google-genai client."""
    types = pytest.importorskip("google.genai.types")
    from viser.extras import GeminiBackend

    def chunk(parts: list) -> Any:
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
        )

    class _Models:
        def __init__(self) -> None:
            self.requests: list[tuple] = []

        async def generate_content_stream(self, *, model, contents, config):
            self.requests.append((model, contents, config))

            async def gen():
                yield chunk([types.Part(text="Adding it.")])
                yield chunk(
                    [
                        types.Part(
                            function_call=types.FunctionCall(
                                id="c1", name="add_box", args={"name": "b"}
                            )
                        )
                    ]
                )

            return gen()

    class _Client:
        def __init__(self) -> None:
            self.aio = type("_Aio", (), {"models": _Models()})()

    client = _Client()
    backend = GeminiBackend("sys prompt", client=client)
    conv = viser.Conversation()
    conv.append(viser.ChatMessage("user", "make a box"))

    history = backend.start_history(conv)
    written: list[str] = []
    turn = asyncio.run(
        backend.stream_turn(history, "gemini-x", [_Tool("add_box")], written.append)
    )
    assert written == ["Adding it."]
    assert [(c.name, c.arguments, c.call_id) for c in turn.tool_calls] == [
        ("add_box", {"name": "b"}, "c1")
    ]

    extended = backend.extend_history(
        history, turn, [ToolResult(turn.tool_calls[0], '{"success": true}', False)]
    )
    assert [c.role for c in extended] == ["user", "model", "user"]
    # Every part of the model turn (thought signatures included) is sent back.
    assert len(extended[-2].parts) == 2
    assert extended[-1].parts[0].function_response.name == "add_box"

    _, _, config = client.aio.models.requests[0]
    assert config.system_instruction == "sys prompt"
    assert config.tools[0].function_declarations[0].name == "add_box"


def test_unrecognized_errors_fall_back_to_a_generic_message() -> None:
    server = viser.ViserServer(port=0, verbose=False)
    try:

        class _Boom(_Backend):
            async def stream_turn(self, *args: Any, **kwargs: Any) -> ModelTurn:
                raise RuntimeError("connection refused")

        chat = server.gui.add_chat()
        # format_error returns None: the assistant reports the exception itself.
        McpAssistant(ENDPOINT, _Boom([]), model="m").connect(chat)
        _run(chat)

        text = [m.text for m in chat.messages if m.role == "system"][0]
        assert ENDPOINT in text and "RuntimeError: connection refused" in text
    finally:
        server.stop()


def test_a_broken_format_error_does_not_mask_the_real_failure() -> None:
    """A backend whose SDK is missing still reports the underlying error."""
    server = viser.ViserServer(port=0, verbose=False)
    try:

        class _Boom(_Backend):
            async def stream_turn(self, *args: Any, **kwargs: Any) -> ModelTurn:
                raise RuntimeError("connection refused")

            def format_error(self, error: BaseException) -> Any:
                raise ImportError("no sdk installed")

        chat = server.gui.add_chat()
        McpAssistant(ENDPOINT, _Boom([]), model="m").connect(chat)
        _run(chat)

        text = [m.text for m in chat.messages if m.role == "system"][0]
        assert "RuntimeError: connection refused" in text
    finally:
        server.stop()
