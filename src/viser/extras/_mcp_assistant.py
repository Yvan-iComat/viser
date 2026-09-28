"""Chat assistant that drives an MCP server's tools, for any LLM provider."""

from __future__ import annotations

import json
import traceback
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

import viser

if TYPE_CHECKING:
    from mcp.types import Tool as McpTool  # pyright: ignore[reportMissingImports]

DEFAULT_MAX_TOOL_ROUNDS = 10


class ToolCall:
    """A tool call requested by the model.

    Args:
        name: Name of the tool to call.
        arguments: Arguments to call it with.
        call_id: Provider-specific identifier, echoed back with the result.
    """

    def __init__(
        self,
        name: str,
        arguments: dict[str, Any],
        call_id: Optional[str] = None,
    ) -> None:
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class ToolResult:
    """The outcome of running a :class:`ToolCall` against the MCP server.

    Args:
        call: The call this answers.
        text: Text content returned by the tool.
        is_error: Whether the tool reported a failure.
    """

    def __init__(self, call: ToolCall, text: str, is_error: bool) -> None:
        self.call = call
        self.text = text
        self.is_error = is_error


class ModelTurn:
    """One assistant turn: text written so far, plus any tool calls to run.

    Args:
        text: Text the model produced this turn (already streamed to the chat).
        tool_calls: Tools the model wants to call before continuing.
        state: Opaque provider-native representation of the turn. The tool
            loop never inspects it; it is handed back to
            :meth:`LlmBackend.extend_history` so providers that require their
            own turn objects (e.g. Gemini's thought signatures) round-trip
            them unchanged.
    """

    def __init__(
        self,
        text: str = "",
        tool_calls: Sequence[ToolCall] = (),
        state: Any = None,
    ) -> None:
        self.text = text
        self.tool_calls = list(tool_calls)
        self.state = state


@runtime_checkable
class LlmBackend(Protocol):
    """Provider-specific half of :class:`McpAssistant`.

    A backend converts between the chat's conversation and whatever the
    provider's SDK expects, and streams one assistant turn at a time. The tool
    loop, the MCP session and all error reporting live in
    :class:`McpAssistant`.
    """

    def start_history(self, conversation: viser.Conversation) -> Any:
        """Build the provider's initial conversation state from chat history."""
        ...

    def extend_history(
        self, history: Any, turn: ModelTurn, results: Sequence[ToolResult]
    ) -> Any:
        """Append a model turn and its tool results, returning the new history."""
        ...

    async def stream_turn(
        self,
        history: Any,
        model: str,
        tools: Sequence[McpTool],
        write: Any,
    ) -> ModelTurn:
        """Stream one assistant turn, calling ``write(text)`` as text arrives.

        Args:
            history: Conversation state from :meth:`start_history` or
                :meth:`extend_history`.
            model: Model to answer with.
            tools: Tools advertised by the MCP server.
            write: Callback appending text to the reply.

        Returns:
            The turn, including any tool calls the model requested.
        """
        ...

    def format_error(self, error: BaseException) -> Optional[str]:
        """Message to show for a provider-specific error, or None to re-raise."""
        ...


def _reports_failure(text: str) -> bool:
    """Whether a tool's JSON result has ``"success": false``."""
    try:
        result = json.loads(text)
    except ValueError:
        return False
    return isinstance(result, dict) and result.get("success") is False


class McpAssistant:
    """Answer chat messages with an LLM, letting it call an MCP server's tools.

    For each message an MCP client session is opened, the server's tools are
    offered to the model, and the model's tool calls are forwarded to the
    server until it produces a final answer. The provider is supplied as an
    :class:`LlmBackend`, so the same loop works for Gemini, Claude or any other
    tool-calling model.

    Example:
        >>> from viser.extras import GeminiBackend, McpAssistant
        >>>
        >>> assistant = McpAssistant(endpoint, GeminiBackend(system_instruction))
        >>> assistant.connect(chat)

    Args:
        endpoint: Streamable HTTP URL of the MCP server.
        backend: Provider adapter used to answer.
        model: Default model; the chat's model selector overrides it.
        max_tool_rounds: Safety net for runaway tool loops within one reply.
    """

    def __init__(
        self,
        endpoint: str,
        backend: LlmBackend,
        model: str = "",
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> None:
        self.endpoint = endpoint
        self.backend = backend
        self.model = model
        self.max_tool_rounds = max_tool_rounds

    async def answer(self, event: viser.ChatSubmitEvent) -> None:
        """Chat submit callback: run the tool loop, streaming text and tool calls."""
        from mcp import Client as McpClient  # pyright: ignore[reportMissingImports]

        try:
            async with McpClient(self.endpoint) as mcp_client:
                tools = (await mcp_client.list_tools()).tools
                model = event.model if event.model != "" else self.model
                history = self.backend.start_history(event.conversation)
                with event.chat.stream() as reply:
                    await self._tool_loop(mcp_client, model, tools, history, reply)
        except Exception as e:
            # A backend that can't classify the error must not mask it.
            try:
                message = self.backend.format_error(e)
            except Exception:
                message = None
            if message is None:
                traceback.print_exc()
                # The MCP client's task groups wrap errors, e.g. a refused
                # connection, in exception groups: report the first leaf.
                while len(getattr(e, "exceptions", ())) > 0:
                    e = e.exceptions[0]  # type: ignore[attr-defined]
                message = (
                    f"Assistant failed ({self.endpoint}): {type(e).__name__}: {e}"
                )
            event.chat.add_message("system", message)

    async def _tool_loop(
        self,
        mcp_client: Any,
        model: str,
        tools: Sequence[McpTool],
        history: Any,
        reply: viser.ChatStream,
    ) -> None:
        for _ in range(self.max_tool_rounds):
            turn = await self.backend.stream_turn(history, model, tools, reply.write)
            if len(turn.tool_calls) == 0:
                return
            results = [
                await self._run_tool(mcp_client, call, reply)
                for call in turn.tool_calls
            ]
            history = self.backend.extend_history(history, turn, results)
        reply.write(f"\n\n*Stopped after {self.max_tool_rounds} rounds of tool calls.*")

    async def _run_tool(
        self, mcp_client: Any, call: ToolCall, reply: viser.ChatStream
    ) -> ToolResult:
        """Run one tool call and note it in the reply."""
        result = await mcp_client.call_tool(call.name, call.arguments)
        text = "\n".join(
            c.text for c in result.content if getattr(c, "type", "") == "text"
        )
        is_error = bool(result.is_error) or _reports_failure(text)
        status = "failed" if is_error else "ok"
        reply.write(
            f"\n\n> `{call.name}({json.dumps(call.arguments)})` — {status}\n\n"
        )
        return ToolResult(call, text, is_error)

    def connect(self, chat: viser.GuiChatHandle) -> None:
        """Answer every message submitted to ``chat``."""
        chat.on_submit(self.answer)


class GeminiBackend:
    """:class:`LlmBackend` for Google Gemini, via ``google-genai``.

    Requires ``pip install google-genai`` and a ``GEMINI_API_KEY`` (or
    ``GOOGLE_API_KEY``) environment variable, unless ``client`` is given.

    Args:
        system_instruction: System prompt for answers.
        client: Optional preconfigured ``google.genai.Client``.
    """

    def __init__(self, system_instruction: str = "", client: Any = None) -> None:
        if client is None:
            try:
                from google.genai import Client  # pyright: ignore[reportMissingImports]
            except ImportError as e:
                raise ImportError(
                    "GeminiBackend requires google-genai: `pip install google-genai`."
                ) from e
            client = Client()
        self.client = client
        self.system_instruction = system_instruction

    def start_history(self, conversation: viser.Conversation) -> list[Any]:
        from google.genai import types  # pyright: ignore[reportMissingImports]

        return [
            types.Content(
                role="user" if m.role == "user" else "model",
                parts=[types.Part(text=m.text or "(see attachment)")],
            )
            for m in conversation.messages
            if m.role in ("user", "assistant")
        ]

    def extend_history(
        self, history: list[Any], turn: ModelTurn, results: Sequence[ToolResult]
    ) -> list[Any]:
        from google.genai import types  # pyright: ignore[reportMissingImports]

        # `turn.state` holds every part of the model's turn (including thought
        # signatures), which Gemini expects back alongside the results.
        return [
            *history,
            types.Content(role="model", parts=turn.state),
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=r.call.call_id,
                            name=r.call.name,
                            response={
                                "error" if r.is_error else "result": r.text
                            },
                        )
                    )
                    for r in results
                ],
            ),
        ]

    async def stream_turn(
        self,
        history: list[Any],
        model: str,
        tools: Sequence[McpTool],
        write: Any,
    ) -> ModelTurn:
        from google.genai import types  # pyright: ignore[reportMissingImports]

        config = types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters_json_schema=tool.input_schema,
                        )
                        for tool in tools
                    ]
                )
            ],
        )
        parts: list[Any] = []
        text: list[str] = []
        async for chunk in await self.client.aio.models.generate_content_stream(
            model=model, contents=history, config=config
        ):
            content = chunk.candidates[0].content if chunk.candidates else None
            for part in (content.parts if content else None) or ():
                parts.append(part)
                if part.text and not part.thought:
                    text.append(part.text)
                    write(part.text)
        calls = [p.function_call for p in parts if p.function_call is not None]
        return ModelTurn(
            text="".join(text),
            tool_calls=[
                ToolCall(c.name or "", dict(c.args or {}), c.id) for c in calls
            ],
            state=parts,
        )

    def format_error(self, error: BaseException) -> Optional[str]:
        from google.genai import errors  # pyright: ignore[reportMissingImports]

        if isinstance(error, errors.APIError):
            return f"Gemini API error {error.code}: {error.message}"
        return None


class AnthropicBackend:
    """:class:`LlmBackend` for Claude, via the ``anthropic`` SDK.

    Requires ``pip install anthropic`` and an ``ANTHROPIC_API_KEY``
    environment variable, unless ``client`` is given.

    Args:
        system_instruction: System prompt for answers.
        max_tokens: Output token limit per turn.
        client: Optional preconfigured ``anthropic.AsyncAnthropic``.
    """

    def __init__(
        self,
        system_instruction: str = "",
        max_tokens: int = 8192,
        client: Any = None,
    ) -> None:
        if client is None:
            try:
                import anthropic  # pyright: ignore[reportMissingImports]
            except ImportError as e:
                raise ImportError(
                    "AnthropicBackend requires anthropic: `pip install anthropic`."
                ) from e
            client = anthropic.AsyncAnthropic()
        self.client = client
        self.system_instruction = system_instruction
        self.max_tokens = max_tokens

    def start_history(self, conversation: viser.Conversation) -> list[dict]:
        return [
            {"role": m.role, "content": m.text or "(see attachment)"}
            for m in conversation.messages
            if m.role in ("user", "assistant")
        ]

    def extend_history(
        self, history: list[dict], turn: ModelTurn, results: Sequence[ToolResult]
    ) -> list[dict]:
        return [
            *history,
            {"role": "assistant", "content": turn.state},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.call_id,
                        "content": r.text,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            },
        ]

    async def stream_turn(
        self,
        history: list[dict],
        model: str,
        tools: Sequence[McpTool],
        write: Any,
    ) -> ModelTurn:
        blocks: list[dict] = []
        text: list[str] = []
        async with self.client.messages.stream(
            model=model,
            max_tokens=self.max_tokens,
            system=self.system_instruction,
            messages=history,
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ],
        ) as stream:
            async for delta in stream.text_stream:
                text.append(delta)
                write(delta)
            final = await stream.get_final_message()
        for block in final.content:
            blocks.append(block.model_dump(exclude_none=True))
        return ModelTurn(
            text="".join(text),
            tool_calls=[
                ToolCall(b["name"], dict(b.get("input") or {}), b.get("id"))
                for b in blocks
                if b.get("type") == "tool_use"
            ],
            state=blocks,
        )

    def format_error(self, error: BaseException) -> Optional[str]:
        import anthropic  # pyright: ignore[reportMissingImports]

        if isinstance(error, anthropic.APIStatusError):
            return f"Anthropic API error {error.status_code}: {error.message}"
        if isinstance(error, anthropic.APIConnectionError):
            return "Could not reach the Anthropic API."
        return None
