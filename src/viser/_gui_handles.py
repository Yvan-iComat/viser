from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import io
import json
import math
import re
import threading
import time
import uuid
import warnings
from collections.abc import Coroutine, Mapping
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    Iterable,
    Iterator,
    Literal,
    Sequence,
    Tuple,
    TypeVar,
    Union,
    cast,
    overload,
)

import numpy as np
from typing_extensions import Protocol, Self, TypeAlias, override

from ._assignable_props_api import AssignablePropsBase
from ._chat import (
    ChatAttachment,
    ChatMessage,
    ChatRole,
    Conversation,
    ConversationStore,
)
from ._icons import svg_from_icon
from ._icons_enum import IconName
from ._messages import (
    CommandProps,
    CommandUpdateMessage,
    EdgePlacement,
    FloatPlacement,
    GuiBaseProps,
    GuiButtonGroupProps,
    GuiButtonProps,
    GuiChatAttachment,
    GuiChatConversationInfo,
    GuiChatEntry,
    GuiChatProps,
    GuiCheckboxProps,
    GuiCloseModalMessage,
    GuiColorbarProps,
    GuiDividerProps,
    GuiDropdownProps,
    GuiFolderProps,
    GuiFolderSelectButtonProps,
    GuiFormSubmitMessage,
    GuiGalleryBlock,
    GuiGalleryProps,
    GuiGalleryRenderRequestMessage,
    GuiHtmlProps,
    GuiImageProps,
    GuiMarkdownProps,
    GuiMultiSliderProps,
    GuiNodeGraphEdge,
    GuiNodeGraphMenuItem,
    GuiNodeGraphNode,
    GuiNodeGraphNodeAction,
    GuiNodeGraphNodeStatus,
    GuiNodeGraphNodeType,
    GuiNodeGraphPort,
    GuiNodeGraphProps,
    GuiNumberProps,
    GuiPanelProps,
    GuiPanelRemoveMessage,
    GuiPlotlyProps,
    GuiProgressBarProps,
    GuiRemoveMessage,
    GuiRgbaProps,
    GuiRgbProps,
    GuiSetPanelCollapsedMessage,
    GuiSetPanelHeightMessage,
    GuiSetPanelPositionMessage,
    GuiSetPanelWidthMessage,
    GuiSliderProps,
    GuiTabGroupProps,
    GuiTableDataProps,
    GuiTextProps,
    GuiUpdateMessage,
    GuiUploadButtonProps,
    GuiUplotProps,
    GuiVector2Props,
    GuiVector3Props,
    RemoveCommandMessage,
    SplitPlacement,
    TimelineProps,
    TimelineRemoveMessage,
)
from ._node_graph import NodePort, coerce_value, dtypes_compatible, value_kind
from ._scene_api import _encode_image_binary
from ._threadpool_exceptions import print_awaited_callback_error, print_task_error
from .infra import ClientId

if TYPE_CHECKING:
    import plotly.graph_objects as go

    from ._gui_api import GuiApi
    from ._viser import ClientHandle


T = TypeVar("T")
TGuiHandle = TypeVar("TGuiHandle", bound="_GuiHandle")
NoneOrCoroutine = TypeVar("NoneOrCoroutine", None, Coroutine)


def _make_uuid() -> str:
    """Return a unique ID for referencing GUI elements."""
    return str(uuid.uuid4())


class GuiContainerProtocol(Protocol):
    _children: dict[str, SupportsRemoveProtocol] = dataclasses.field(
        default_factory=dict
    )


class SupportsRemoveProtocol(Protocol):
    def remove(self) -> None: ...


def _cascade_remove(child: SupportsRemoveProtocol) -> None:
    """Remove a child as part of a parent's removal cascade, silently
    skipping children that a concurrent disconnect teardown already
    tombstoned -- remove() would warn "already removed" for a purely
    internal race the user did not cause.

    Best-effort: the unlocked check narrows the race window rather than
    closing it (a teardown landing between this check and the removed-guard
    inside remove() still warns, harmlessly). Closing it would need a lock
    shared across all GUI removal paths, which the design avoids."""
    impl = getattr(child, "_impl", child)  # Tabs carry `removed` directly.
    if getattr(impl, "removed", False):
        return
    child.remove()


class GuiPropsProtocol(Protocol):
    order: float


@dataclasses.dataclass
class _GuiHandleState(Generic[T]):
    """Internal API for GUI elements."""

    uuid: str
    gui_api: GuiApi
    value: T
    props: GuiPropsProtocol
    parent_container_id: str
    """Container that this GUI input was placed into."""

    update_timestamp: float = 0.0
    update_cb: list[Callable[[GuiEvent], None | Coroutine]] = dataclasses.field(
        default_factory=list
    )
    """Registered functions to call when this input is updated."""

    is_button: bool = False
    """Indicates a button element, which requires special handling."""

    sync_cb: Callable[[ClientId, dict[str, Any]], None] | None = None
    """Callback for synchronizing inputs across clients."""

    removed: bool = False


@dataclasses.dataclass
class _GuiButtonHandleState(_GuiHandleState[bool]):
    """Internal API for button GUI elements with hold callback support."""

    hold_cbs_from_freq: dict[float, list[Callable[[GuiEvent], None | Coroutine]]] = (
        dataclasses.field(default_factory=dict)
    )
    """Mapping from frequency (Hz) to list of callbacks to call when button is held."""


# Not exported for now because some GUI handles don't currently inhert from
# `_GuiHandle`: notably `GuiModalHandle` and `GuiTabHandle`. These would fail
# isinstance checks, which would be confusing!
class _GuiHandle(Generic[T], AssignablePropsBase[_GuiHandleState]):
    def __init__(self, impl: _GuiHandleState[T]) -> None:
        super().__init__(impl=impl)
        gui_api = self._impl.gui_api
        parent = gui_api._resolve_container_handle(self._impl.parent_container_id)
        parent._children[self._impl.uuid] = self
        if self._impl.parent_container_id not in gui_api._container_handle_from_uuid:
            # Client element nested inside a server container: the parent
            # lives in the server GuiApi's registry, so this scope's reset()
            # and disconnect teardown can't find it through the root
            # container walk. Track it for those paths.
            gui_api._handles_in_foreign_containers[self._impl.uuid] = self

        if isinstance(self, _GuiInputHandle):
            gui_api._gui_input_handle_from_uuid[self._impl.uuid] = self

    @override
    def _queue_update(self, name: str, value: Any) -> None:
        self._impl.gui_api._websock_interface.queue_message(
            GuiUpdateMessage(self._impl.uuid, {name: value})
        )

    def remove(self) -> None:
        """Permanently remove this GUI element from the visualizer."""

        # Warn if already removed.
        if self._impl.removed:
            warnings.warn(
                f"Attempted to remove an already removed {self.__class__.__name__}.",
                stacklevel=2,
            )
            return
        self._impl.removed = True

        gui_api = self._impl.gui_api
        gui_api._websock_interface.queue_message(GuiRemoveMessage(self._impl.uuid))
        # Tolerant detach: a disconnect teardown racing this remove() may
        # have already purged the parent and these registry entries
        # (bookkeeping-only, no lock).
        try:
            parent = gui_api._resolve_container_handle(self._impl.parent_container_id)
            parent._children.pop(self._impl.uuid, None)
        except KeyError:
            pass
        gui_api._handles_in_foreign_containers.pop(self._impl.uuid, None)

        if isinstance(self, _GuiInputHandle):
            gui_api._gui_input_handle_from_uuid.pop(self._impl.uuid, None)


class _GuiInputHandle(
    _GuiHandle[T],
    Generic[T],
    GuiBaseProps,
):
    @property
    def value(self) -> T:
        """Value of the GUI input. Synchronized automatically when assigned.

        :meta private:
        """
        # ^Note: we mark this property as private for Sphinx because I haven't
        # been able to get it to resolve the TypeVar in a readable way.
        # For the documentation's sake, we'll be manually adding ::attribute directives below.
        return self._impl.value

    def _coerce_assigned_value(self, value: T | np.ndarray) -> T | np.ndarray:
        """Hook for input-type-specific coercion of an assigned value. The base
        is identity; rgb/rgba handles override this to normalize colors."""
        return value

    @value.setter
    def value(self, value: T | np.ndarray) -> None:
        value = self._coerce_assigned_value(value)
        if isinstance(value, np.ndarray):
            assert len(value.shape) <= 1, f"{value.shape} should be at most 1D!"
            # Preserve each element's expected Python type -- float for vectors,
            # int for colors. A blanket `float(...)` would turn an int tuple into
            # floats, and the `tuple(...)` cast below does not restore types.
            #
            # When the target type is int (bool excluded) and the incoming
            # element is a float, ROUND instead of casting: `int(2.9)`
            # truncates toward zero, which silently corrupts values that were
            # meant to be near-integers. We use the round() builtin, which is
            # round-half-even ("banker's rounding": 0.5 -> 0, 1.5 -> 2) --
            # exact .5 inputs are rare for this path and half-even is the
            # documented Python convention.
            elems = value.tolist()
            current = self._impl.value
            if isinstance(current, tuple) and len(current) == len(elems):
                value = tuple(
                    type(c)(round(e))
                    if type(c) is int and isinstance(e, float)
                    else type(c)(e)
                    for c, e in zip(current, elems)
                )  # type: ignore
            else:
                value = tuple(elems)  # type: ignore

        # Convert to internal type early so we can compare.
        value = type(self._impl.value)(value)  # type: ignore

        # Skip if value hasn't changed (but always process buttons).
        if not self._impl.is_button:
            try:
                if self._impl.value == value:
                    return
            except (TypeError, ValueError):
                pass

        # Send to client, except for buttons.
        if not self._impl.is_button:
            self._impl.gui_api._websock_interface.queue_message(
                GuiUpdateMessage(self._impl.uuid, {"value": value})
            )

        # Set internal state.
        self._impl.value = value  # type: ignore
        self._impl.update_timestamp = time.time()

        # Call update callbacks.
        for cb in self._impl.update_cb:
            # As a design decision: we choose to call update callbacks
            # synchronously instead of in the thread pool. It's rare that there
            # are significant blocking callbacks for GUI updates; this also
            # reduces the likelihood of many common race conditions.
            cb_out = cb(GuiEvent(client_id=None, client=None, target=self))
            if isinstance(cb_out, Coroutine):
                # run_coroutine_threadsafe, not create_task: assignment
                # typically happens on a user thread, and create_task is
                # neither thread-safe nor guaranteed to wake the loop.
                asyncio.run_coroutine_threadsafe(
                    cb_out, self._impl.gui_api._event_loop
                ).add_done_callback(print_task_error)

    @property
    def update_timestamp(self) -> float:
        """Read-only timestamp when this input was last updated."""
        return self._impl.update_timestamp


StringType = TypeVar("StringType", bound=str)


# GuiInputHandle[T] is used for all inputs except for buttons.
#
# We inherit from _GuiInputHandle to special-case buttons because the usage semantics
# are slightly different: we have `on_click()` instead of `on_update()`.
class GuiInputHandle(_GuiInputHandle[T], Generic[T]):
    """A handle is created for each GUI element that is added in `viser`.
    Handles can be used to read and write state.

    When a GUI element is added via :attr:`ViserServer.gui`, state is
    synchronized between all connected clients. When a GUI element is added via
    :attr:`ClientHandle.gui`, state is local to a specific client.
    """

    def on_update(
        self: TGuiHandle, func: Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]
    ) -> Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]:
        """Attach a function to call when a GUI input is updated.

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.
        """
        self._impl.update_cb.append(func)
        return func

    def remove_update_callback(
        self, callback: Literal["all"] | Callable = "all"
    ) -> None:
        """Remove update callbacks from the GUI input.

        Args:
            callback: Either "all" to remove all callbacks, or a specific callback function to remove.
        """
        if callback == "all":
            self._impl.update_cb.clear()
        else:
            self._impl.update_cb = [cb for cb in self._impl.update_cb if cb != callback]


class GuiCheckboxHandle(GuiInputHandle[bool], GuiCheckboxProps):
    """Handle for checkbox inputs.

    .. attribute:: value
       :type: bool

       Value of the input. Synchronized automatically when assigned.
    """


class GuiTextHandle(GuiInputHandle[str], GuiTextProps):
    """Handle for text inputs.

    .. attribute:: value
       :type: str

       Value of the input. Synchronized automatically when assigned.
    """


IntOrFloat = TypeVar("IntOrFloat", int, float)


class GuiNumberHandle(GuiInputHandle[IntOrFloat], Generic[IntOrFloat], GuiNumberProps):
    """Handle for number inputs.

    .. attribute:: value
       :type: IntOrFloat

       Value of the input. Synchronized automatically when assigned.
    """


class GuiSliderHandle(GuiInputHandle[IntOrFloat], Generic[IntOrFloat], GuiSliderProps):
    """Handle for slider inputs.

    .. attribute:: value
       :type: IntOrFloat

       Value of the input. Synchronized automatically when assigned.
    """


class GuiMultiSliderHandle(
    GuiInputHandle[Tuple[IntOrFloat, ...]], Generic[IntOrFloat], GuiMultiSliderProps
):
    """Handle for multi-slider inputs.

    .. attribute:: value
       :type: tuple[IntOrFloat, ...]

       Value of the input. Synchronized automatically when assigned.
    """


def _colors_to_int_tuple(value: Any, *, warn_stacklevel: int) -> tuple[int, ...]:
    """Coerce an RGB/RGBA color to an int tuple in [0, 255].

    Integer channels are taken as absolute [0, 255]; float channels are
    interpreted as [0, 1] and scaled (the matplotlib convention), so ``1.0`` ->
    255 (white) but ``1`` -> 1. The result is clamped to [0, 255] -- matching
    ``colors_to_uint8`` -- so out-of-range inputs (e.g. a float ``255.0`` or a
    negative value) degrade gracefully instead of producing a wild value.
    Generalized to any channel count (RGB and RGBA).

    Float channels > 1.0 emit a warning: before viser 1.1.0 they were passed
    through unchanged (a float ``100.0`` behaved like the int ``100``), so old
    code passing float [0, 255] colors now silently clamps to white without
    it. ``warn_stacklevel`` must point the warning at USER code, and every call
    path has a different depth (required, no default, so a new call site has to
    count its own frames): ``add_rgb``/``add_rgba`` need 4 -- user ->
    deprecated_positional_shim wrapper -> add_rgb* -> here -- and the ``.value``
    assignment path needs 5."""
    if isinstance(value, np.ndarray):
        assert value.ndim == 1, f"Expected a 1D color, got shape {value.shape}."
    # Materialize once up front: we iterate twice (the float-channel check and
    # the tuple build below), so a generator input would arrive exhausted at
    # the second pass and silently yield an empty/partial color.
    value = tuple(value)
    if any(not np.issubdtype(type(v), np.integer) and v > 1.0 for v in value):
        warnings.warn(
            "Float color channels are interpreted on [0, 1] and scaled to "
            f"[0, 255]; values > 1.0 are clamped to 255 (got {value!r}). "
            "Use ints for absolute [0, 255] channels.",
            stacklevel=warn_stacklevel,
        )
    return tuple(
        max(0, min(255, int(v) if np.issubdtype(type(v), np.integer) else int(v * 255)))
        for v in value
    )


class GuiRgbHandle(GuiInputHandle[Tuple[int, int, int]], GuiRgbProps):
    """Handle for RGB color inputs.

    .. attribute:: value
       :type: tuple[int, int, int]

       Value of the input. Synchronized automatically when assigned.
    """

    @override
    def _coerce_assigned_value(
        self, value: Tuple[int, int, int] | np.ndarray
    ) -> Tuple[int, int, int]:
        # Float channels are [0, 1] (scaled to [0, 255]); int channels absolute.
        # warn_stacklevel: user assignment -> props_setattr -> value.fset ->
        # this method -> _colors_to_int_tuple = 5 frames.
        return cast(
            Tuple[int, int, int], _colors_to_int_tuple(value, warn_stacklevel=5)
        )


class GuiRgbaHandle(GuiInputHandle[Tuple[int, int, int, int]], GuiRgbaProps):
    """Handle for RGBA color inputs.

    .. attribute:: value
       :type: tuple[int, int, int, int]

       Value of the input. Synchronized automatically when assigned.
    """

    @override
    def _coerce_assigned_value(
        self, value: Tuple[int, int, int, int] | np.ndarray
    ) -> Tuple[int, int, int, int]:
        # Float channels are [0, 1] (scaled to [0, 255]); int channels absolute.
        # warn_stacklevel: user assignment -> props_setattr -> value.fset ->
        # this method -> _colors_to_int_tuple = 5 frames.
        return cast(
            Tuple[int, int, int, int], _colors_to_int_tuple(value, warn_stacklevel=5)
        )


class GuiVector2Handle(GuiInputHandle[Tuple[float, float]], GuiVector2Props):
    """Handle for 2D vector inputs.

    .. attribute:: value
       :type: tuple[float, float]

       Value of the input. Synchronized automatically when assigned.
    """


class GuiVector3Handle(GuiInputHandle[Tuple[float, float, float]], GuiVector3Props):
    """Handle for 3D vector inputs.

    .. attribute:: value
       :type: tuple[float, float, float]

       Value of the input. Synchronized automatically when assigned.
    """


@dataclasses.dataclass(frozen=True)
class GuiEvent(Generic[TGuiHandle]):
    """Information associated with a GUI event, such as an update or click.

    Passed as input to callback functions."""

    client: ClientHandle | None
    """Client that triggered this event."""
    client_id: int | None
    """ID of client that triggered this event."""
    target: TGuiHandle
    """GUI element that was affected."""


class GuiButtonHandle(_GuiInputHandle[bool], GuiButtonProps):
    """Handle for a button input in our visualizer.

    .. attribute:: value
       :type: bool

       Value of the button. Set to `True` when the button is pressed. Can be manually set back to `False`.
    """

    def __init__(self, _impl: _GuiButtonHandleState, _icon: IconName | None):
        super().__init__(impl=_impl)
        self._icon = _icon

    @property
    def _button_impl(self) -> _GuiButtonHandleState:
        """Access the button-specific implementation state."""
        assert isinstance(self._impl, _GuiButtonHandleState)
        return self._impl

    @property
    def icon(self) -> IconName | None:
        """Icon to display on the button. When set to None, no icon is displayed."""
        return self._icon

    @icon.setter
    def icon(self, icon: IconName | None) -> None:
        self._icon = icon
        self._icon_html = None if icon is None else svg_from_icon(icon)

    def on_click(
        self: TGuiHandle, func: Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]
    ) -> Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]:
        """Attach a function to call when a button is pressed.

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.
        """
        self._impl.update_cb.append(func)
        return func

    # Type alias for button hold callbacks.
    _HoldCallback = Callable[["GuiEvent[GuiButtonHandle]"], "None | Coroutine"]

    @overload
    def on_hold(
        self,
        func: None = None,
        callback_hz: float = 10.0,
    ) -> Callable[[_HoldCallback], _HoldCallback]: ...

    @overload
    def on_hold(
        self,
        func: _HoldCallback,
        callback_hz: float = 10.0,
    ) -> _HoldCallback: ...

    def on_hold(
        self,
        func: _HoldCallback | None = None,
        callback_hz: float = 10.0,
    ) -> Callable[[_HoldCallback], _HoldCallback] | _HoldCallback:
        """Attach a function to call repeatedly while a button is held down.

        The callback will be triggered immediately when the button is pressed,
        and then repeatedly at the specified frequency until released.

        Can be used as a decorator with or without arguments:
            @button.on_hold
            def callback(event): ...

            @button.on_hold(callback_hz=30.0)
            def callback(event): ...

        Or called directly:
            button.on_hold(callback)
            button.on_hold(callback, callback_hz=30.0)

        Args:
            func: The callback function to attach. If None, returns a decorator.
            callback_hz: The frequency in Hz at which to call the callback while
                the button is held. Defaults to 10.0 Hz.

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.
        """
        button_impl = self._button_impl

        def register_callback(
            f: GuiButtonHandle._HoldCallback,
        ) -> GuiButtonHandle._HoldCallback:
            # Add callback to the frequency-specific list.
            if callback_hz not in button_impl.hold_cbs_from_freq:
                button_impl.hold_cbs_from_freq[callback_hz] = []
            button_impl.hold_cbs_from_freq[callback_hz].append(f)

            # Update the prop to notify client of new frequency.
            self._hold_callback_freqs = tuple(button_impl.hold_cbs_from_freq.keys())

            return f

        if func is not None:
            return register_callback(func)
        return register_callback


@dataclasses.dataclass
class UploadedFile:
    """Result of a file upload."""

    name: str
    """Name of the file."""
    content: bytes
    """Contents of the file."""


class GuiUploadButtonHandle(_GuiInputHandle[UploadedFile], GuiUploadButtonProps):
    """Handle for an upload file button in our visualizer.

    The `.value` attribute will be updated with the contents of uploaded files.

    .. attribute:: value
       :type: UploadedFile

       Value of the input. Contains information about the uploaded file.
    """

    def __init__(self, _impl: _GuiHandleState[UploadedFile], _icon: IconName | None):
        super().__init__(impl=_impl)
        self._icon = _icon

    @property
    def icon(self) -> IconName | None:
        """Icon to display on the upload button. When set to None, no icon is displayed."""
        return self._icon

    @icon.setter
    def icon(self, icon: IconName | None) -> None:
        self._icon = icon
        self._icon_html = None if icon is None else svg_from_icon(icon)

    def on_upload(
        self: TGuiHandle, func: Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]
    ) -> Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]:
        """Attach a function to call when a file is uploaded.

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.
        """
        self._impl.update_cb.append(func)
        return func


class GuiFolderSelectButtonHandle(_GuiInputHandle[str], GuiFolderSelectButtonProps):
    """Handle for a folder select button in our visualizer.

    The `.value` attribute will be updated with the absolute path of the selected folder.

    .. attribute:: value
       :type: str

       Value of the input. Contains the absolute path of the selected folder.
    """

    def __init__(self, _impl: _GuiHandleState[str], _icon: IconName | None):
        super().__init__(impl=_impl)
        self._icon = _icon

    @property
    def icon(self) -> IconName | None:
        """Icon to display on the folder select button. When set to None, no icon is displayed."""
        return self._icon

    @icon.setter
    def icon(self, icon: IconName | None) -> None:
        self._icon = icon
        self._icon_html = None if icon is None else svg_from_icon(icon)

    def on_select(
        self: TGuiHandle, func: Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]
    ) -> Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]:
        """Attach a function to call when a folder is selected.

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.
        """
        self._impl.update_cb.append(func)
        return func


class GuiButtonGroupHandle(_GuiInputHandle[str], GuiButtonGroupProps):
    """Handle for a button group input in our visualizer.

    .. attribute:: value
       :type: str

       Value of the input. Represents the currently selected button in the group.
    """

    def on_click(
        self: TGuiHandle, func: Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]
    ) -> Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]:
        """Attach a function to call when a button in the group is clicked.

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.
        """
        self._impl.update_cb.append(func)
        return func

    @property
    def disabled(self) -> bool:
        """Button groups cannot be disabled."""
        return False

    @disabled.setter
    def disabled(self, disabled: bool) -> None:  # type: ignore
        """Button groups cannot be disabled."""
        assert not disabled, "Button groups cannot be disabled."


class GuiDropdownHandle(
    GuiInputHandle[StringType], Generic[StringType], GuiDropdownProps
):
    """Handle for a dropdown-style GUI input in our visualizer.

    .. attribute:: value
       :type: StringType

       Value of the input. Represents the currently selected option in the dropdown.
    """

    @property
    def options(self) -> tuple[StringType, ...]:
        """Options for our dropdown. Synchronized automatically when assigned.

        For projects that care about typing: the static type of `options` should be
        consistent with the `StringType` associated with a handle. Literal types will be
        inferred where possible when handles are instantiated; for the most flexibility,
        we can declare handles as `GuiDropdownHandle[str]`.
        """
        assert isinstance(self._impl.props, GuiDropdownProps)
        return self._impl.props.options  # type: ignore

    @options.setter
    def options(self, options: Iterable[StringType]) -> None:  # type: ignore
        assert isinstance(self._impl.props, GuiDropdownProps)
        options = tuple(options)
        if len(options) == 0:
            raise ValueError("Dropdown requires at least one option.")
        self._impl.props.options = options

        self._impl.gui_api._websock_interface.queue_message(
            GuiUpdateMessage(
                self._impl.uuid,
                {"options": options},
            )
        )
        if self.value not in options:
            self.value = options[0]


TableData = Tuple[Tuple[Union[str, float, int], ...], ...]


class GuiTableDataHandle(GuiInputHandle[TableData], GuiTableDataProps):
    """Handle for table data component.

    The table stores data as rows and columns. Cells can be strings or numbers
    depending on column configuration.

    .. attribute:: value
       :type: Tuple[Tuple[Union[str, float, int], ...], ...]

       Current table data as tuple of rows. Each row is a tuple of cell values.
       Synchronized automatically when assigned.

    Example:
        >>> # Access current data
        >>> current_data = table.value
        >>>
        >>> # Replace entire table
        >>> table.value = (("A", 1), ("B", 2))
    """

    def clear_rows(self) -> None:
        """Remove all rows from the table, keeping column structure."""
        self.value = ()

    def append_row(self, row: Sequence[str | float | int]) -> None:
        """Append a new row to the end of the table.

        Args:
            row: Sequence of cell values matching column count and types.

        Raises:
            ValueError: If row length doesn't match column count or types are invalid.
        """
        row_tuple = tuple(row)

        # Validate row
        if len(row_tuple) != len(self.columns):
            raise ValueError(
                f"Row has {len(row_tuple)} cells but table has {len(self.columns)} columns"
            )

        for col_idx, (cell, col_def) in enumerate(zip(row_tuple, self.columns)):
            if col_def.cell_type == "number" and not isinstance(cell, (int, float)):
                raise ValueError(
                    f"Cell at column {col_idx} ('{col_def.title}') must be numeric, got {type(cell).__name__}"
                )

        # Append to current data
        self.value = self.value + (row_tuple,)

    def set_cell(self, row: int, col: int, value: str | float | int) -> None:
        """Update a single cell value.

        Args:
            row: Row index (0-based).
            col: Column index (0-based).
            value: New cell value.

        Raises:
            IndexError: If row or column index is out of bounds.
            ValueError: If value type doesn't match column type.
        """
        if row < 0 or row >= len(self.value):
            raise IndexError(
                f"Row index {row} out of bounds (table has {len(self.value)} rows)"
            )
        if col < 0 or col >= len(self.columns):
            raise IndexError(
                f"Column index {col} out of bounds (table has {len(self.columns)} columns)"
            )

        col_def = self.columns[col]
        if col_def.cell_type == "number" and not isinstance(value, (int, float)):
            raise ValueError(
                f"Column {col} ('{col_def.title}') requires numeric value, got {type(value).__name__}"
            )

        # Create new data structure with updated cell
        new_rows = list(self.value)
        row_list = list(new_rows[row])
        row_list[col] = value
        new_rows[row] = tuple(row_list)
        self.value = tuple(new_rows)

    def delete_row(self, row: int) -> None:
        """Delete a row from the table.

        Args:
            row: Row index (0-based) to delete.

        Raises:
            IndexError: If row index is out of bounds.
        """
        if row < 0 or row >= len(self.value):
            raise IndexError(
                f"Row index {row} out of bounds (table has {len(self.value)} rows)"
            )

        new_rows = list(self.value)
        del new_rows[row]
        self.value = tuple(new_rows)

    def on_select_row(
        self: TGuiHandle, func: Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]
    ) -> Callable[[GuiEvent[TGuiHandle]], NoneOrCoroutine]:
        """Attach a callback for when a user selects a table row.

        The selected row index is available via `event.target.selected_row`.
        When a row is deselected, the value will be -1.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.

        Example:
            >>> @table.on_select_row
            >>> def handle_selection(event):
            >>>     row_idx = event.target.selected_row
            >>>     if row_idx >= 0:
            >>>         row_data = event.target.value[row_idx]
            >>>         print(f"Selected row {row_idx}: {row_data}")
        """
        self._impl.update_cb.append(func)
        return func

    @property
    def selected_row(self) -> int:
        """Index of currently selected row, or -1 if no row is selected.

        Only meaningful when selection_mode is 'single'.
        """
        # This will be stored as an internal property updated by frontend
        return getattr(self._impl, "_selected_row", -1)


class GalleryBlockHandle:
    """Handle for a single block (card) within a gallery.

    Blocks are not standalone GUI components: the gallery owns all block state
    in its ``blocks`` prop, and this handle is a façade that reads and rewrites
    the entry matching its ``block_id``. Mutating a removed block is a no-op.
    """

    def __init__(self, gallery: GuiGalleryHandle, block_id: str) -> None:
        self._gallery = gallery
        self._block_id = block_id
        self._click_cb: list[Callable[[GalleryBlockEvent], None | Coroutine]] = []

    @property
    def block_id(self) -> str:
        """Stable identifier for this block."""
        return self._block_id

    @property
    def removed(self) -> bool:
        """Whether this block has been removed from its gallery."""
        return self._gallery._find_block(self._block_id) is None

    @property
    def title(self) -> str:
        """Title text shown in the block footer. Synchronized when assigned."""
        block = self._gallery._find_block(self._block_id)
        return "" if block is None else block.title

    @title.setter
    def title(self, title: str) -> None:
        self._gallery._mutate_block(self._block_id, title=title)

    @property
    def subtitle(self) -> str | None:
        """Optional subtitle shown below the title. Synchronized when assigned."""
        block = self._gallery._find_block(self._block_id)
        return None if block is None else block.subtitle

    @subtitle.setter
    def subtitle(self, subtitle: str | None) -> None:
        self._gallery._mutate_block(self._block_id, subtitle=subtitle)

    def set_image(self, image: np.ndarray) -> None:
        """Replace this block's snapshot with a numpy image.

        Args:
            image: (H, W, 3) or (H, W, 4) uint8 array.
        """
        image_format, data = _encode_image_binary(image, "auto")
        self._gallery._mutate_block(self._block_id, _data=data, _format=image_format)

    def remove(self) -> None:
        """Remove this block from its gallery."""
        self._gallery.remove_block(self)

    def on_click(
        self, func: Callable[[GalleryBlockEvent], NoneOrCoroutine]
    ) -> Callable[[GalleryBlockEvent], NoneOrCoroutine]:
        """Attach a callback for when this block is left clicked.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Example:
            >>> @block.on_click
            >>> def _(event):
            >>>     print("clicked", event.target.title)
        """
        self._click_cb.append(func)
        return func


@dataclasses.dataclass(frozen=True)
class GalleryBlockEvent:
    """Information associated with a gallery block click.

    Passed as input to callback functions."""

    client: ClientHandle | None
    """Client that triggered this event."""
    client_id: int | None
    """ID of client that triggered this event."""
    gallery: GuiGalleryHandle
    """Gallery containing the clicked block."""
    target: GalleryBlockHandle
    """Block that was clicked."""

    @property
    def block_id(self) -> str:
        """Identifier of the block that was clicked."""
        return self.target.block_id


class GuiGalleryHandle(_GuiHandle[None], GuiGalleryProps):
    """Handle for a gallery of clickable image blocks arranged in a grid.

    The grid's column count is derived by the client from ``block_width`` and
    the available width, so the gallery reflows to fill the window.
    """

    def __init__(self, _impl: _GuiHandleState) -> None:
        super().__init__(impl=_impl)
        self._block_handles: dict[str, GalleryBlockHandle] = {}
        self._gallery_click_cb: list[
            Callable[[GalleryBlockEvent], None | Coroutine]
        ] = []
        # Pending STL renders, keyed by render_uuid, awaiting a client reply.
        self._pending_renders: dict[str, tuple[str, threading.Event]] = {}

    def _find_block(self, block_id: str) -> GuiGalleryBlock | None:
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        return None

    def _mutate_block(self, block_id: str, **changes: Any) -> None:
        """Rewrite one block in-place, resyncing the whole ``blocks`` prop.

        Silently ignores unknown ids so callbacks racing a removal are safe.
        """
        new_blocks = tuple(
            dataclasses.replace(block, **changes)
            if block.block_id == block_id
            else block
            for block in self.blocks
        )
        # Only assign when something actually matched, so a stale handle does
        # not trigger a redundant broadcast.
        if any(block.block_id == block_id for block in self.blocks):
            self.blocks = new_blocks

    def add_block(
        self,
        title: str,
        subtitle: str | None = None,
        *,
        image: np.ndarray | None = None,
        stl: str | Path | bytes | None = None,
        block_id: str | None = None,
        timeout: float | None = 10.0,
    ) -> GalleryBlockHandle:
        """Add a block to the gallery.

        Exactly one of ``image`` or ``stl`` must be provided.

        Args:
            title: Title text, shown in the block footer.
            subtitle: Optional subtitle, shown below the title.
            image: Snapshot as an (H, W, 3) or (H, W, 4) uint8 numpy array.
            stl: Path to (or raw bytes of) an STL mesh. A snapshot is rendered
                from it in the browser; requires a connected client.
            block_id: Optional stable identifier. Generated if omitted.
            timeout: Seconds to wait for an STL snapshot before giving up. The
                block is still added; its image stays blank on timeout.

        Returns:
            A handle for manipulating the new block.
        """
        if (image is None) == (stl is None):
            raise ValueError("Exactly one of `image` or `stl` must be provided.")

        block_id = block_id if block_id is not None else _make_uuid()
        if self._find_block(block_id) is not None:
            raise ValueError(f"Gallery already contains a block with id {block_id!r}.")

        if image is not None:
            image_format, data = _encode_image_binary(image, "auto")
            stl_data = None
        else:
            # Snapshot is filled in by the client; start blank. Validate the
            # mesh up front so a bad file raises instead of leaving behind a
            # permanently blank block.
            assert stl is not None
            image_format, data = "png", None
            stl_data = self._load_stl_bytes(block_id, stl)

        self.blocks = self.blocks + (
            GuiGalleryBlock(
                block_id=block_id,
                title=title,
                subtitle=subtitle,
                _data=data,
                _format=image_format,
            ),
        )
        handle = GalleryBlockHandle(self, block_id)
        self._block_handles[block_id] = handle

        if stl_data is not None:
            self._request_stl_snapshot(block_id, stl_data, timeout=timeout)

        return handle

    def _load_stl_bytes(self, block_id: str, stl: str | Path | bytes) -> bytes:
        """Read and validate an STL, raising before any block state is touched."""
        stl_data = stl if isinstance(stl, bytes) else Path(stl).read_bytes()

        # Parse to fail fast on malformed input, with a clearer error than the
        # browser could give us. trimesh is already a hard dependency.
        import trimesh

        try:
            parsed = trimesh.load(io.BytesIO(stl_data), file_type="stl")
        except Exception as e:
            raise ValueError(f"Could not parse STL for block {block_id!r}: {e}") from e

        # trimesh doesn't raise on unparseable STL bytes -- it hands back an
        # empty Scene. Without this check a malformed file would silently
        # become a blank block after the render timeout elapsed.
        if parsed.is_empty:
            raise ValueError(
                f"Could not parse STL for block {block_id!r}: no geometry found."
            )
        return stl_data

    def _request_stl_snapshot(
        self, block_id: str, stl_data: bytes, timeout: float | None
    ) -> None:
        """Ask a client to render an STL into this block's snapshot."""
        render_uuid = _make_uuid()
        done = threading.Event()
        self._pending_renders[render_uuid] = (block_id, done)
        self._impl.gui_api._websock_interface.queue_message(
            GuiGalleryRenderRequestMessage(
                uuid=self._impl.uuid,
                render_uuid=render_uuid,
                _stl_data=stl_data,
                width=self.block_width,
                height=self.block_height,
            )
        )
        if timeout is not None:
            # Wait for the snapshot so callers see a populated block on return.
            # A miss is not fatal: the reply still lands if it arrives late.
            if not done.wait(timeout):
                num_clients = len(
                    getattr(
                        self._impl.gui_api._websock_interface,
                        "_client_state_from_id",
                        {},
                    )
                )
                hint = (
                    "No client is connected: STL snapshots are rendered in the "
                    "browser, so add these blocks from an `on_client_connect` "
                    "callback, or pass `image=` instead."
                    if num_clients == 0
                    else f"{num_clients} client(s) connected but none replied in "
                    f"{timeout}s; try a longer `timeout`."
                )
                warnings.warn(
                    f"Timed out waiting for STL snapshot of block {block_id!r}. "
                    f"The block was added without an image. {hint}"
                )

    def _handle_render_reply(self, render_uuid: str, data: bytes | None) -> None:
        """Apply a client-rendered snapshot to its block."""
        pending = self._pending_renders.pop(render_uuid, None)
        if pending is None:
            return
        block_id, done = pending
        if data is not None:
            self._mutate_block(block_id, _data=data, _format="png")
        done.set()

    def remove_block(self, block: GalleryBlockHandle | str) -> None:
        """Remove a block from the gallery.

        Args:
            block: The block handle, or its ``block_id``. Unknown ids are ignored.
        """
        block_id = block if isinstance(block, str) else block.block_id
        self.blocks = tuple(b for b in self.blocks if b.block_id != block_id)
        self._block_handles.pop(block_id, None)

    def clear(self) -> None:
        """Remove all blocks from the gallery."""
        self.blocks = ()
        self._block_handles.clear()

    def on_click(
        self, func: Callable[[GalleryBlockEvent], NoneOrCoroutine]
    ) -> Callable[[GalleryBlockEvent], NoneOrCoroutine]:
        """Attach a callback for when any block in this gallery is clicked.

        Fires after any per-block callbacks. The clicked block is available as
        ``event.target``.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._gallery_click_cb.append(func)
        return func


@dataclasses.dataclass(frozen=True)
class ChatSubmitEvent:
    """Information associated with a chat message submitted by a user.

    Passed as input to :meth:`GuiChatHandle.on_submit` callbacks."""

    client: ClientHandle | None
    """Client that submitted the message."""
    client_id: int | None
    """ID of the client that submitted the message."""
    chat: GuiChatHandle
    """Chat the message was submitted to."""
    conversation: Conversation
    """Conversation the message was appended to. Its ``messages`` list holds
    the full history, including the submitted message."""
    message: ChatMessage
    """The submitted user message."""
    model: str
    """Model selected in the chat when the message was submitted (empty if
    the chat has no models). Captured at submission, so it stays stable even
    if the user switches models while the reply is generated."""


class ChatStream:
    """An assistant reply being written incrementally.

    Created by :meth:`GuiChatHandle.stream`; the text is committed to the
    conversation when the ``with`` block exits."""

    def __init__(self, chat: GuiChatHandle) -> None:
        self._chat = chat
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        """Text written so far."""
        return "".join(self._parts)

    def write(self, delta: str) -> None:
        """Append text to the reply and display it."""
        if delta == "":
            return
        self._parts.append(delta)
        self._chat._set_props(streaming_text=self.text)


class GuiChatHandle(_GuiHandle[None], GuiChatProps):
    """Handle for an AI-assistant style chat.

    The chat only transports messages: attach a callback with
    :meth:`on_submit` to generate replies, using :meth:`add_message` or
    :meth:`stream`. Conversations are persisted through a
    :class:`ConversationStore`, and can be browsed from the history drawer.
    """

    def __init__(self, _impl: _GuiHandleState, store: ConversationStore) -> None:
        super().__init__(impl=_impl)
        self._store = store
        self._conversation = Conversation()
        self._lock = threading.RLock()
        self._submit_cb: list[Callable[[ChatSubmitEvent], None | Coroutine]] = []
        # Strong references to in-flight reply tasks, so they aren't GC'd.
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def store(self) -> ConversationStore:
        """Store used to persist conversations."""
        return self._store

    @property
    def conversation(self) -> Conversation:
        """Conversation currently displayed."""
        return self._conversation

    def _set_props(self, **changes: Any) -> None:
        """Assign several props in one update message, so clients apply them
        together (e.g. committing a streamed reply and clearing the stream)."""
        if self._impl.removed:
            return
        for name, value in changes.items():
            setattr(self._impl.props, name, value)
        self._impl.gui_api._websock_interface.queue_message(
            GuiUpdateMessage(self._impl.uuid, changes)
        )

    @staticmethod
    def _entry(message: ChatMessage) -> GuiChatEntry:
        return GuiChatEntry(
            message_id=message.message_id,
            role=message.role,
            text=message.text,
            attachments=tuple(
                GuiChatAttachment(a.name, a.mime_type, a.thumbnail)
                for a in message.attachments
            ),
            timestamp=message.timestamp,
        )

    def _conversation_infos(self) -> tuple[GuiChatConversationInfo, ...]:
        return tuple(
            GuiChatConversationInfo(c.conversation_id, c.title, c.updated_at)
            for c in self._store.list_conversations()
        )

    def _sync(self, *, conversations: bool = True, **extra: Any) -> None:
        """Push the active conversation (and optionally the history list)."""
        changes: dict[str, Any] = {
            "messages": tuple(self._entry(m) for m in self._conversation.messages),
            "active_conversation_id": self._conversation.conversation_id,
            **extra,
        }
        if conversations:
            changes["conversations"] = self._conversation_infos()
        self._set_props(**changes)

    def _commit(self, message: ChatMessage, **extra: Any) -> None:
        with self._lock:
            is_first = len(self._conversation.messages) == 0
            self._conversation.append(message)
            self._store.save(self._conversation)
            # The history list only changes when a conversation is first
            # saved (which also sets its title) -- i.e. on its first message.
            self._sync(conversations=is_first, **extra)

    def add_message(
        self,
        role: ChatRole,
        text: str,
        attachments: Sequence[ChatAttachment] = (),
    ) -> ChatMessage:
        """Append a message to the active conversation and save it.

        Args:
            role: ``"assistant"``, ``"user"``, or ``"system"``.
            text: Message text. Assistant and system messages render as markdown.
            attachments: Optional files attached to the message.

        Returns:
            The appended message.
        """
        message = ChatMessage(role=role, text=text, attachments=list(attachments))
        self._commit(message)
        return message

    @contextlib.contextmanager
    def stream(self, role: ChatRole = "assistant") -> Iterator[ChatStream]:
        """Stream a reply into the chat, e.g. token by token from an LLM.

        The text is displayed as it is written, and appended to the active
        conversation when the block exits (also on error, if any text was
        written).

        Example:
            >>> with chat.stream() as reply:
            >>>     for token in llm_tokens():
            >>>         reply.write(token)
        """
        reply = ChatStream(self)
        self._set_props(streaming_text="")
        try:
            yield reply
        finally:
            if reply.text != "":
                self._commit(
                    ChatMessage(role=role, text=reply.text), streaming_text=None
                )
            else:
                self._set_props(streaming_text=None)

    def new_conversation(self) -> Conversation:
        """Start a new, empty conversation and display it.

        The conversation is only saved once it has a message."""
        with self._lock:
            self._conversation = Conversation()
            self._sync()
            return self._conversation

    def open_conversation(self, conversation_id: str) -> Conversation:
        """Load a saved conversation and display it.

        Raises:
            KeyError: If no conversation with this id exists.
        """
        with self._lock:
            conversation = self._store.load(conversation_id)
            if conversation is None:
                raise KeyError(f"No conversation with id {conversation_id!r}.")
            self._conversation = conversation
            self._sync()
            return conversation

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete a saved conversation. If it is displayed, a new one is started."""
        with self._lock:
            self._store.delete(conversation_id)
            if conversation_id == self._conversation.conversation_id:
                self._conversation = Conversation()
            self._sync()

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        """Change the title of a saved conversation. Unknown ids are ignored."""
        with self._lock:
            if conversation_id == self._conversation.conversation_id:
                conversation: Conversation | None = self._conversation
            else:
                conversation = self._store.load(conversation_id)
            if conversation is None or len(conversation.messages) == 0:
                return
            conversation.title = title.strip() or conversation.title
            self._store.save(conversation)
            self._set_props(conversations=self._conversation_infos())

    @override
    def _on_prop_assigned(self, name: str) -> None:
        # Keep the selection valid when the model list changes.
        if name == "models" and self.model not in self.models:
            self.model = self.models[0] if len(self.models) > 0 else ""

    def on_submit(
        self, func: Callable[[ChatSubmitEvent], NoneOrCoroutine]
    ) -> Callable[[ChatSubmitEvent], NoneOrCoroutine]:
        """Attach a callback for when the user submits a message.

        By the time the callback runs, the user's message has been appended to
        ``event.conversation`` and displayed. The chat shows a busy state
        (and blocks new submissions) until all callbacks return.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Example:
            >>> @chat.on_submit
            >>> def _(event: viser.ChatSubmitEvent) -> None:
            >>>     event.chat.add_message("assistant", f"You said: {event.message.text}")
        """
        self._submit_cb.append(func)
        return func

    def _handle_submit(
        self,
        client: ClientHandle,
        client_id: ClientId,
        text: str,
        attachments: list[ChatAttachment],
    ) -> None:
        """Record a client submission, then run the callbacks in the background."""
        if self.busy or (text.strip() == "" and len(attachments) == 0):
            return
        message = ChatMessage(role="user", text=text, attachments=attachments)
        self._commit(message, busy=True)
        event = ChatSubmitEvent(
            client=client,
            client_id=client_id,
            chat=self,
            conversation=self._conversation,
            message=message,
            model=self.model,
        )
        # Run as a task: awaiting here would block this client's websocket
        # handler, so history actions couldn't be processed during a reply.
        task = asyncio.get_running_loop().create_task(self._run_submit_callbacks(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(print_task_error)

    async def _run_submit_callbacks(self, event: ChatSubmitEvent) -> None:
        gui_api = self._impl.gui_api
        loop = asyncio.get_running_loop()
        try:
            for cb in self._submit_cb:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(event)
                    else:
                        await loop.run_in_executor(gui_api._thread_executor, cb, event)
                except Exception as e:
                    print_awaited_callback_error(e)
        finally:
            self._set_props(busy=False, streaming_text=None)

    def _handle_action(
        self,
        action: Literal["new", "open", "delete", "rename", "set_model"],
        conversation_id: str,
        value: str,
    ) -> None:
        if action == "set_model":
            # Only models offered by the server can be selected.
            if value in self.models:
                self.model = value
            else:
                warnings.warn(f"Ignoring selection of unknown model {value!r}.")
            return
        # Switching conversations mid-reply would send the reply to the wrong
        # place; the client disables history while busy, this is the backstop.
        if self.busy and action in ("new", "open", "delete"):
            return
        try:
            if action == "new":
                self.new_conversation()
            elif action == "open":
                self.open_conversation(conversation_id)
            elif action == "delete":
                self.delete_conversation(conversation_id)
            elif action == "rename":
                self.rename_conversation(conversation_id, value)
        except (KeyError, ValueError) as e:
            warnings.warn(f"Chat history action {action!r} failed: {e}")


@dataclasses.dataclass(frozen=True)
class NodeGraphNodeEvent:
    """Information associated with a user action on a node of a node graph.

    Passed as input to :meth:`GuiNodeGraphHandle.on_node_double_click`,
    :meth:`GuiNodeGraphHandle.on_node_edit`,
    :meth:`GuiNodeGraphHandle.on_node_add`,
    :meth:`GuiNodeGraphHandle.on_node_remove` and
    :meth:`NodeGraphActionHandle.on_click` callbacks."""

    client: ClientHandle | None
    """Client that acted on the node. None when the node was added, or
    removed, from Python."""
    client_id: int | None
    """ID of the client that acted on the node."""
    graph: GuiNodeGraphHandle
    """Graph the node belongs to."""
    node_id: str
    """ID of the node."""
    type_name: str
    """Node type of the node."""


NodeGraphCallback: TypeAlias = Callable[[NodeGraphNodeEvent], Union[None, Coroutine]]


class NodeGraphRun:
    """Outcome of a :meth:`GuiNodeGraphHandle.node_running` block, shown in
    the node when the block ends. Success unless reported otherwise."""

    def __init__(self) -> None:
        self._state: Literal["success", "error"] = "success"
        self._message: str | None = None

    def success(self, message: str | None = None) -> None:
        """Report success, with an optional message shown on hover."""
        self._state, self._message = "success", message

    def error(self, message: str) -> None:
        """Report a failure, e.g. invalid inputs, without raising. The
        message is shown on hover."""
        self._state, self._message = "error", message


@dataclasses.dataclass(frozen=True)
class NodeGraphInputEvent:
    """A change to one input of a node: the user edited its value, or a wire
    was plugged into it or unplugged from it.

    Passed as input to :meth:`GuiNodeGraphHandle.on_node_input_change`
    callbacks."""

    client: ClientHandle | None
    """Client whose edit in the browser made the change. None for changes made
    from Python, including from inside other callbacks."""
    client_id: int | None
    """ID of the client whose edit made the change."""
    graph: GuiNodeGraphHandle
    """Graph the node belongs to."""
    node_id: str
    """ID of the node whose input changed."""
    type_name: str
    """Node type of the node."""
    port: str
    """Name of the input port that changed."""
    reason: Literal["value", "connected", "disconnected"]
    """``"value"`` when the input's own value was edited, ``"connected"`` or
    ``"disconnected"`` when a wire was plugged into it or unplugged from it.
    Replacing the wire into an input reports both, in that order."""
    value: bool | int | float | str | None
    """The input's own value (see :attr:`NodePort.default`), used while no
    wire is plugged in. None for inputs that only take wires."""
    edge: GuiNodeGraphEdge | None
    """The wire that was plugged or unplugged; None for ``"value"``."""


@dataclasses.dataclass(frozen=True)
class NodeGraphChangeEvent:
    """What one edit of a node graph changed.

    Passed as input to :meth:`GuiNodeGraphHandle.on_change` callbacks. Removing
    a node also removes its wires, so both show up in the same event."""

    client: ClientHandle | None
    """Client whose edit in the browser made the change. None for changes made
    from Python, including from inside other callbacks."""
    client_id: int | None
    """ID of the client whose edit made the change."""
    graph: GuiNodeGraphHandle
    """Graph that changed."""
    added_nodes: Tuple[str, ...]
    """IDs of the nodes that were added."""
    removed_nodes: Tuple[str, ...]
    """IDs of the nodes that were removed."""
    moved_nodes: Tuple[str, ...]
    """IDs of the nodes that were moved."""
    added_edges: Tuple[str, ...]
    """IDs of the wires that were added."""
    removed_edges: Tuple[str, ...]
    """IDs of the wires that were removed."""
    changed_values: Tuple[Tuple[str, str], ...]
    """``(node_id, port)`` of each input whose own value was edited."""


@dataclasses.dataclass(frozen=True)
class _NodeGraphChange:
    """Difference between two states of a node graph."""

    added_nodes: Tuple[GuiNodeGraphNode, ...]
    removed_nodes: Tuple[GuiNodeGraphNode, ...]
    moved_nodes: Tuple[str, ...]
    added_edges: Tuple[GuiNodeGraphEdge, ...]
    removed_edges: Tuple[GuiNodeGraphEdge, ...]
    changed_values: Tuple[Tuple[str, str], ...]

    @staticmethod
    def between(
        old_nodes: Tuple[GuiNodeGraphNode, ...],
        old_edges: Tuple[GuiNodeGraphEdge, ...],
        new_nodes: Tuple[GuiNodeGraphNode, ...],
        new_edges: Tuple[GuiNodeGraphEdge, ...],
    ) -> _NodeGraphChange | None:
        """Returns None when nothing changed."""
        old_from_id = {n.node_id: n for n in old_nodes}
        new_node_ids = {n.node_id for n in new_nodes}
        old_edge_ids = {e.edge_id for e in old_edges}
        new_edge_ids = {e.edge_id for e in new_edges}
        kept = [
            (old_from_id[n.node_id], n) for n in new_nodes if n.node_id in old_from_id
        ]
        change = _NodeGraphChange(
            added_nodes=tuple(n for n in new_nodes if n.node_id not in old_from_id),
            removed_nodes=tuple(n for n in old_nodes if n.node_id not in new_node_ids),
            moved_nodes=tuple(
                new.node_id for old, new in kept if old.position != new.position
            ),
            added_edges=tuple(e for e in new_edges if e.edge_id not in old_edge_ids),
            removed_edges=tuple(e for e in old_edges if e.edge_id not in new_edge_ids),
            changed_values=tuple(
                (new.node_id, port)
                for old, new in kept
                for port, value in new.values.items()
                # Compare types too: True == 1, but a bool is not an int.
                if (type(value), value)
                != (type(old.values.get(port)), old.values.get(port))
            ),
        )
        return None if change == _NO_CHANGE else change


_NO_CHANGE = _NodeGraphChange((), (), (), (), (), ())


class NodeGraphActionHandle:
    """A custom entry of a node graph's node context menu.

    Created by :meth:`GuiNodeGraphHandle.add_node_action`."""

    def __init__(self, graph: GuiNodeGraphHandle, action_id: str) -> None:
        self._graph = graph
        self._action_id = action_id
        self._click_cb: list[NodeGraphCallback] = []

    def on_click(
        self, func: Callable[[NodeGraphNodeEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphNodeEvent], NoneOrCoroutine]:
        """Attach a callback for when the action is picked from a node's
        context menu.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._click_cb.append(func)
        return func

    def remove(self) -> None:
        """Remove the action from the context menu."""
        graph = self._graph
        with graph._lock:
            graph._action_handles.pop(self._action_id, None)
            graph._set_props(
                node_actions=tuple(
                    a for a in graph.node_actions if a.action_id != self._action_id
                )
            )


@dataclasses.dataclass(frozen=True)
class NodeGraphCanvasEvent:
    """Information associated with a click on an entry of a node graph's
    canvas context menu.

    Passed as input to :meth:`NodeGraphMenuItemHandle.on_click` callbacks."""

    client: ClientHandle | None
    """Client that clicked the entry."""
    client_id: int | None
    """ID of the client that clicked the entry."""
    graph: GuiNodeGraphHandle
    """Graph whose canvas was right-clicked."""
    position: Tuple[float, float]
    """Canvas position that was right-clicked, in the same coordinates as node
    positions -- e.g. to place a new node with
    :meth:`GuiNodeGraphHandle.add_node`."""


class NodeGraphMenuItemHandle:
    """An entry of a node graph's canvas context menu.

    Created by :meth:`GuiNodeGraphHandle.add_canvas_menu_item`. An entry that
    has entries of its own is shown as a submenu."""

    def __init__(self, graph: GuiNodeGraphHandle, item_id: str) -> None:
        self._graph = graph
        self._item_id = item_id
        self._click_cb: list[
            Callable[[NodeGraphCanvasEvent], Union[None, Coroutine]]
        ] = []

    def add_item(
        self, label: str, *, icon: IconName | None = None
    ) -> NodeGraphMenuItemHandle:
        """Add an entry to this entry's submenu, turning it into one."""
        return self._graph.add_canvas_menu_item(label, icon=icon, parent=self)

    def add_divider(self) -> NodeGraphMenuItemHandle:
        """Add a separator line to this entry's submenu."""
        return self._graph.add_canvas_menu_divider(parent=self)

    def on_click(
        self, func: Callable[[NodeGraphCanvasEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphCanvasEvent], NoneOrCoroutine]:
        """Attach a callback for when the entry is clicked. Entries shown as a
        submenu open it instead, and never fire.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._click_cb.append(func)
        return func

    def remove(self) -> None:
        """Remove the entry from the menu, along with its submenu."""
        graph = self._graph
        with graph._lock:
            removed = {self._item_id}
            # Items are stored parents-first, so one pass finds every descendant.
            for item in graph.canvas_menu:
                if item.parent_id in removed:
                    removed.add(item.item_id)
            for item_id in removed:
                graph._menu_item_handles.pop(item_id, None)
            graph._set_props(
                canvas_menu=tuple(
                    i for i in graph.canvas_menu if i.item_id not in removed
                )
            )


class GuiNodeGraphHandle(_GuiHandle[None], GuiNodeGraphProps):
    """Handle for a node graph editor: typed nodes joined by wires.

    Register the kinds of node with :meth:`add_node_type`, then build the graph
    with :meth:`add_node` and :meth:`connect`. Users can also edit the graph in
    the browser -- move, wire and delete nodes -- and these edits are applied
    to the handle's state.

    Right-clicking a node opens a context menu with the built-in Edit (see
    :meth:`on_node_edit`) and Delete entries, followed by custom actions added
    with :meth:`add_node_action`. Right-clicking the empty canvas opens a menu
    built with :meth:`add_canvas_menu_item`.

    Every edit, from the browser or from Python, is reported to the callbacks
    of :meth:`on_node_add`, :meth:`on_node_remove`,
    :meth:`on_node_input_change` and :meth:`on_change`, in that order: regular
    callbacks of one edit run one after the other.
    """

    def __init__(self, _impl: _GuiHandleState) -> None:
        super().__init__(impl=_impl)
        self._lock = threading.RLock()
        self._double_click_cb: list[NodeGraphCallback] = []
        self._edit_cb: list[NodeGraphCallback] = []
        self._node_add_cb: list[NodeGraphCallback] = []
        self._node_remove_cb: list[NodeGraphCallback] = []
        self._input_change_cb: list[
            Callable[[NodeGraphInputEvent], Union[None, Coroutine]]
        ] = []
        self._change_cb: list[
            Callable[[NodeGraphChangeEvent], Union[None, Coroutine]]
        ] = []
        # Initial input values of each node type, by type and port name.
        self._defaults: dict[str, dict[str, bool | int | float | str]] = {}
        # Number of unfinished node_running() blocks, by node ID.
        self._runs: dict[str, int] = {}
        self._action_handles: dict[str, NodeGraphActionHandle] = {}
        self._menu_item_handles: dict[str, NodeGraphMenuItemHandle] = {}

    def _set_props(self, **changes: Any) -> None:
        """Assign several props in one update message, so clients apply them
        together (e.g. removing a node and the wires attached to it)."""
        if self._impl.removed:
            return
        for name, value in changes.items():
            setattr(self._impl.props, name, value)
        self._impl.gui_api._websock_interface.queue_message(
            GuiUpdateMessage(self._impl.uuid, changes)
        )

    def _set_graph(
        self,
        *,
        nodes: Tuple[GuiNodeGraphNode, ...] | None = None,
        edges: Tuple[GuiNodeGraphEdge, ...] | None = None,
    ) -> _NodeGraphChange | None:
        """Replace the nodes and/or edges, sending nothing when they are
        unchanged. Call with the lock held, then report the returned change
        with :meth:`_report_change` once the lock is released."""
        new_nodes = self.nodes if nodes is None else nodes
        new_edges = self.edges if edges is None else edges
        change = _NodeGraphChange.between(self.nodes, self.edges, new_nodes, new_edges)
        if change is None or self._impl.removed:
            return None
        changes: dict[str, Any] = {}
        if nodes is not None:
            changes["nodes"] = nodes
        if edges is not None:
            changes["edges"] = edges
        if any(n.node_id in self.node_status for n in change.removed_nodes):
            # Statuses of removed nodes go in the same update.
            removed = {n.node_id for n in change.removed_nodes}
            changes["node_status"] = {
                k: v for k, v in self.node_status.items() if k not in removed
            }
        self._set_props(**changes)
        return change

    def _change_callbacks(
        self,
        change: _NodeGraphChange,
        client: ClientHandle | None,
        client_id: int | None,
    ) -> list[tuple[Callable[[Any], Union[None, Coroutine]], Any]]:
        """Pairs of callback and event to run for a change, in order: added
        nodes, removed nodes, changed inputs, then the whole change."""
        calls: list[tuple[Callable[[Any], Union[None, Coroutine]], Any]] = []

        def node_event(node: GuiNodeGraphNode) -> NodeGraphNodeEvent:
            return NodeGraphNodeEvent(
                client=client,
                client_id=client_id,
                graph=self,
                node_id=node.node_id,
                type_name=node.type_name,
            )

        for node in change.added_nodes:
            event = node_event(node)
            calls.extend((cb, event) for cb in self._node_add_cb)
        for node in change.removed_nodes:
            event = node_event(node)
            calls.extend((cb, event) for cb in self._node_remove_cb)

        # Inputs of the nodes still in the graph. Wires into a removed node
        # went with it, so they are not reported.
        node_from_id = {n.node_id: n for n in self.nodes}

        def input_event(
            node_id: str,
            port: str,
            reason: Literal["value", "connected", "disconnected"],
            edge: GuiNodeGraphEdge | None,
        ) -> None:
            node = node_from_id.get(node_id, None)
            if node is None:
                return
            event = NodeGraphInputEvent(
                client=client,
                client_id=client_id,
                graph=self,
                node_id=node_id,
                type_name=node.type_name,
                port=port,
                reason=reason,
                value=node.values.get(port, None),
                edge=edge,
            )
            calls.extend((cb, event) for cb in self._input_change_cb)

        for edge in change.removed_edges:
            input_event(edge.target, edge.target_port, "disconnected", edge)
        for edge in change.added_edges:
            input_event(edge.target, edge.target_port, "connected", edge)
        for node_id, port in change.changed_values:
            input_event(node_id, port, "value", None)

        change_event = NodeGraphChangeEvent(
            client=client,
            client_id=client_id,
            graph=self,
            added_nodes=tuple(n.node_id for n in change.added_nodes),
            removed_nodes=tuple(n.node_id for n in change.removed_nodes),
            moved_nodes=change.moved_nodes,
            added_edges=tuple(e.edge_id for e in change.added_edges),
            removed_edges=tuple(e.edge_id for e in change.removed_edges),
            changed_values=change.changed_values,
        )
        calls.extend((cb, change_event) for cb in self._change_cb)
        return calls

    def _report_change(self, change: _NodeGraphChange | None) -> None:
        """Run the callbacks for an edit made from Python. As for assignments
        to GUI input values, they run on the calling thread, so the edit's
        effects are in place when it returns."""
        if change is None:
            return
        for cb, event in self._change_callbacks(change, None, None):
            cb_out = cb(event)
            if isinstance(cb_out, Coroutine):
                # run_coroutine_threadsafe, not create_task: edits typically
                # happen on a user thread.
                asyncio.run_coroutine_threadsafe(
                    cb_out, self._impl.gui_api._event_loop
                ).add_done_callback(print_task_error)

    def _node_type(self, type_name: str) -> GuiNodeGraphNodeType:
        for node_type in self.node_types:
            if node_type.type_name == type_name:
                return node_type
        raise KeyError(f"Node graph has no node type {type_name!r}.")

    def _node(self, node_id: str) -> GuiNodeGraphNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"Node graph has no node {node_id!r}.")

    def add_node_type(
        self,
        type_name: str,
        *,
        label: str | None = None,
        color: str | None = None,
        inputs: Sequence[NodePort] = (),
        outputs: Sequence[NodePort] = (),
    ) -> None:
        """Register a kind of node that can be placed in the graph.

        Args:
            type_name: Identifier of the node type, used by :meth:`add_node`.
            label: Title shown in the node header. Defaults to ``type_name``.
            color: Accent color of the node header, as a CSS color. Defaults
                to the theme's primary color.
            inputs: Input ports, drawn on the left side of the node. Inputs
                with a :attr:`NodePort.default` value can be edited in the
                node while no wire is plugged into them.
            outputs: Output ports, drawn on the right side of the node.
        """
        for kind, ports in (("input", inputs), ("output", outputs)):
            names = [port.name for port in ports]
            if len(set(names)) != len(names):
                raise ValueError(f"Duplicate {kind} port names in {names!r}.")
        if any(port.default is not None for port in outputs):
            raise ValueError("Only input ports can have a default value.")
        defaults = {
            port.name: port.default for port in inputs if port.default is not None
        }

        def to_wire(ports: Sequence[NodePort]) -> Tuple[GuiNodeGraphPort, ...]:
            return tuple(
                GuiNodeGraphPort(
                    name=port.name,
                    label=port.name if port.label is None else port.label,
                    dtype=port.dtype,
                    value_kind=None
                    if port.default is None
                    else value_kind(port.default),
                )
                for port in ports
            )

        node_type = GuiNodeGraphNodeType(
            type_name=type_name,
            label=type_name if label is None else label,
            color=color,
            inputs=to_wire(inputs),
            outputs=to_wire(outputs),
        )
        with self._lock:
            if any(t.type_name == type_name for t in self.node_types):
                raise ValueError(f"Node type {type_name!r} is already registered.")
            self._defaults[type_name] = defaults
            self._set_props(node_types=self.node_types + (node_type,))

    def add_node(
        self,
        type_name: str,
        position: Tuple[float, float] = (0.0, 0.0),
        *,
        node_id: str | None = None,
        values: Mapping[str, bool | int | float | str] | None = None,
    ) -> str:
        """Place a node in the graph.

        Args:
            type_name: Node type of the new node; see :meth:`add_node_type`.
            position: Position of the node's top-left corner on the canvas,
                in pixels.
            node_id: Optional stable identifier. Generated if omitted.
            values: Initial values of editable inputs, by port name. Inputs
                left out start at their :attr:`NodePort.default`.

        Returns:
            The ID of the new node.
        """
        node_id = _make_uuid() if node_id is None else node_id
        with self._lock:
            node_type = self._node_type(type_name)
            if any(node.node_id == node_id for node in self.nodes):
                raise ValueError(f"Node graph already has a node {node_id!r}.")
            node_values = dict(self._defaults[type_name])
            for port, value in ({} if values is None else values).items():
                node_values[port] = self._checked_value(node_type, port, value)
            node = GuiNodeGraphNode(
                node_id=node_id,
                type_name=type_name,
                position=(float(position[0]), float(position[1])),
                values=node_values,
            )
            change = self._set_graph(nodes=self.nodes + (node,))
        self._report_change(change)
        return node_id

    def remove_node(self, node_id: str) -> None:
        """Remove a node, along with every wire attached to it."""
        with self._lock:
            self._node(node_id)
            change = self._set_graph(
                nodes=tuple(n for n in self.nodes if n.node_id != node_id),
                edges=tuple(
                    e for e in self.edges if node_id not in (e.source, e.target)
                ),
            )
        self._report_change(change)

    def connect(
        self,
        source: str,
        source_port: str,
        target: str,
        target_port: str,
    ) -> str:
        """Wire an output port of one node to an input port of another.

        An input port accepts a single wire, so an existing wire into
        ``target_port`` is replaced.

        Returns:
            The ID of the new edge.
        """
        with self._lock:
            edge_id, change = self._connect(source, source_port, target, target_port)
        self._report_change(change)
        return edge_id

    def _connect(
        self, source: str, source_port: str, target: str, target_port: str
    ) -> tuple[str, _NodeGraphChange | None]:
        """Checks and adds a wire. Returns its ID and the change to report."""
        with self._lock:
            out_port = _find_port(
                self._node_type(self._node(source).type_name).outputs, source_port
            )
            in_port = _find_port(
                self._node_type(self._node(target).type_name).inputs, target_port
            )
            if source == target:
                raise ValueError("Cannot wire a node to itself.")
            if not dtypes_compatible(out_port.dtype, in_port.dtype):
                raise ValueError(
                    f"Cannot wire output {source_port!r} ({out_port.dtype}) to "
                    f"input {target_port!r} ({in_port.dtype})."
                )
            edge = GuiNodeGraphEdge(
                edge_id=_make_uuid(),
                source=source,
                source_port=source_port,
                target=target,
                target_port=target_port,
            )
            change = self._set_graph(
                edges=tuple(
                    e
                    for e in self.edges
                    if not (e.target == target and e.target_port == target_port)
                )
                + (edge,)
            )
        return edge.edge_id, change

    def disconnect(self, edge_id: str) -> None:
        """Remove a wire."""
        with self._lock:
            if not any(e.edge_id == edge_id for e in self.edges):
                raise KeyError(f"Node graph has no edge {edge_id!r}.")
            change = self._set_graph(
                edges=tuple(e for e in self.edges if e.edge_id != edge_id)
            )
        self._report_change(change)

    def _value_kind(
        self, node_type: GuiNodeGraphNodeType, port: str
    ) -> Literal["bool", "int", "float", "str"]:
        kind = _find_port(node_type.inputs, port).value_kind
        if kind is None:
            raise ValueError(
                f"Input {port!r} of {node_type.type_name!r} has no value; give "
                "its NodePort a default to make it editable."
            )
        return kind

    def _checked_value(
        self, node_type: GuiNodeGraphNodeType, port: str, value: Any
    ) -> bool | int | float | str:
        return coerce_value(self._value_kind(node_type, port), value)

    def set_node_status(
        self,
        node_id: str,
        status: Literal["running", "success", "error"] | None,
        message: str | None = None,
    ) -> None:
        """Show the progress of work done for a node in its header: a spinner
        while ``"running"``, then a success or error mark. Hovering the mark
        shows ``message``. ``None`` clears the status.

        :meth:`node_running` sets it around a block of code.
        """
        with self._lock:
            self._node(node_id)
            node_status = dict(self.node_status)
            if status is None:
                node_status.pop(node_id, None)
            else:
                node_status[node_id] = GuiNodeGraphNodeStatus(status, message)
            self._set_props(node_status=node_status)

    @contextlib.contextmanager
    def node_running(self, node_id: str) -> Iterator[NodeGraphRun]:
        """Show a spinner in a node while the ``with`` block runs, then its
        outcome: an error if the block raises -- the exception goes on --,
        else what was reported with :meth:`NodeGraphRun.error` or
        :meth:`NodeGraphRun.success`, else success.

        Nested or concurrent blocks for the same node keep the spinner until
        the last one ends. Nothing is shown if the node is removed meanwhile.

        Example:
            >>> @graph.on_node_input_change
            >>> def _(event: viser.NodeGraphInputEvent) -> None:
            >>>     with graph.node_running(event.node_id) as run:
            >>>         path = compute_toolpath(event.node_id)
            >>>         if path is None:
            >>>             run.error("No toolpath fits the stock.")
            >>>         else:
            >>>             run.success(f"{len(path)} moves")
        """
        run = NodeGraphRun()
        with self._lock:
            self.set_node_status(node_id, "running")
            self._runs[node_id] = self._runs.get(node_id, 0) + 1
        try:
            yield run
        except BaseException as e:
            run.error(f"{type(e).__name__}: {e}")
            raise
        finally:
            with self._lock:
                left = self._runs.get(node_id, 1) - 1
                if left > 0:
                    self._runs[node_id] = left
                else:
                    self._runs.pop(node_id, None)
                if left <= 0 and any(n.node_id == node_id for n in self.nodes):
                    self.set_node_status(node_id, run._state, run._message)

    def get_input_value(self, node_id: str, port: str) -> bool | int | float | str:
        """Value of an editable input of a node, used while no wire is
        plugged into it."""
        with self._lock:
            node = self._node(node_id)
            self._value_kind(self._node_type(node.type_name), port)
            return node.values[port]

    def set_input_value(
        self, node_id: str, port: str, value: bool | int | float | str
    ) -> None:
        """Set the value of an editable input of a node, as if the user had
        edited it in the browser. Ints and floats are converted to the type
        of the input's default value when they fit."""
        with self._lock:
            change = self._set_value(node_id, port, value)
        self._report_change(change)

    def _set_value(
        self, node_id: str, port: str, value: Any
    ) -> _NodeGraphChange | None:
        with self._lock:
            node = self._node(node_id)
            checked = self._checked_value(self._node_type(node.type_name), port, value)
            return self._set_graph(
                nodes=tuple(
                    dataclasses.replace(n, values={**n.values, port: checked})
                    if n.node_id == node_id
                    else n
                    for n in self.nodes
                )
            )

    def add_node_action(
        self,
        label: str,
        *,
        icon: IconName | None = None,
        type_names: Sequence[str] | None = None,
    ) -> NodeGraphActionHandle:
        """Add a custom entry to the node context menu, after Edit and Delete.

        Args:
            label: Text of the menu entry.
            icon: Optional icon shown before the label.
            type_names: Node types the action is offered for. Defaults to every
                node type.

        Returns:
            A handle used to attach callbacks with
            :meth:`NodeGraphActionHandle.on_click`.

        Example:
            >>> run = graph.add_node_action(
            >>>     "Run", icon=viser.Icon.PLAYER_PLAY, type_names=["simulate"]
            >>> )
            >>> @run.on_click
            >>> def _(event: viser.NodeGraphNodeEvent) -> None:
            >>>     print("Running", event.node_id)
        """
        action = GuiNodeGraphNodeAction(
            action_id=_make_uuid(),
            label=label,
            icon_html=None if icon is None else svg_from_icon(icon),
            type_names=None if type_names is None else tuple(type_names),
        )
        handle = NodeGraphActionHandle(self, action.action_id)
        with self._lock:
            self._action_handles[action.action_id] = handle
            self._set_props(node_actions=self.node_actions + (action,))
        return handle

    def add_canvas_menu_item(
        self,
        label: str,
        *,
        icon: IconName | None = None,
        parent: NodeGraphMenuItemHandle | None = None,
    ) -> NodeGraphMenuItemHandle:
        """Add an entry to the context menu opened by right-clicking the empty
        canvas. Entries are shown in the order they are added.

        Args:
            label: Text of the entry.
            icon: Optional icon shown before the label.
            parent: Entry whose submenu the new entry goes in. Defaults to the
                top level of the menu.

        Returns:
            A handle used to attach callbacks with
            :meth:`NodeGraphMenuItemHandle.on_click`, or to add entries to its
            submenu.

        Example:
            >>> add = graph.add_canvas_menu_item("Add node", icon=viser.Icon.PLUS)
            >>> for type_name in ("load", "slice"):
            >>>     item = add.add_item(type_name)
            >>>     @item.on_click
            >>>     def _(event: viser.NodeGraphCanvasEvent, type_name=type_name) -> None:
            >>>         graph.add_node(type_name, event.position)
        """
        return self._add_menu_entry("item", label, icon, parent)

    def add_canvas_menu_divider(
        self, *, parent: NodeGraphMenuItemHandle | None = None
    ) -> NodeGraphMenuItemHandle:
        """Add a separator line to the canvas context menu, or to the submenu of
        ``parent``."""
        return self._add_menu_entry("divider", "", None, parent)

    def _add_menu_entry(
        self,
        kind: Literal["item", "divider"],
        label: str,
        icon: IconName | None,
        parent: NodeGraphMenuItemHandle | None,
    ) -> NodeGraphMenuItemHandle:
        item = GuiNodeGraphMenuItem(
            item_id=_make_uuid(),
            parent_id=None if parent is None else parent._item_id,
            kind=kind,
            label=label,
            icon_html=None if icon is None else svg_from_icon(icon),
        )
        handle = NodeGraphMenuItemHandle(self, item.item_id)
        with self._lock:
            if parent is not None:
                if parent._graph is not self:
                    raise ValueError("`parent` belongs to another node graph.")
                if parent._item_id not in self._menu_item_handles:
                    raise ValueError("`parent` was removed from the menu.")
                if any(
                    i.item_id == parent._item_id and i.kind == "divider"
                    for i in self.canvas_menu
                ):
                    raise ValueError("A divider can't hold a submenu.")
            self._menu_item_handles[item.item_id] = handle
            self._set_props(canvas_menu=self.canvas_menu + (item,))
        return handle

    def on_node_double_click(
        self, func: Callable[[NodeGraphNodeEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphNodeEvent], NoneOrCoroutine]:
        """Attach a callback for when the user double-clicks a node.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._double_click_cb.append(func)
        return func

    def on_node_edit(
        self, func: Callable[[NodeGraphNodeEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphNodeEvent], NoneOrCoroutine]:
        """Attach a callback for the Edit entry of the node context menu, e.g.
        to open the node's settings.

        Note:
            - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._edit_cb.append(func)
        return func

    def on_node_add(
        self, func: Callable[[NodeGraphNodeEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphNodeEvent], NoneOrCoroutine]:
        """Attach a callback for when a node is added with :meth:`add_node`,
        including from callbacks of the canvas menu. The event's client is
        None: nodes are always added from Python.

        Note:
            - The callback runs on the calling thread, before :meth:`add_node`
              returns. Async callbacks are scheduled on the event loop.
        """
        self._node_add_cb.append(func)
        return func

    def on_node_input_change(
        self, func: Callable[[NodeGraphInputEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphInputEvent], NoneOrCoroutine]:
        """Attach a callback for when an input of a node changes: the user
        edits its value in the node, or a wire is plugged into it or unplugged
        from it -- including when the node at the other end is removed. Edits
        made from Python are reported too.

        Note:
            - Edits made from Python run the callback on the calling thread,
              before the editing method returns.
            - Otherwise, if `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._input_change_cb.append(func)
        return func

    def on_node_remove(
        self, func: Callable[[NodeGraphNodeEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphNodeEvent], NoneOrCoroutine]:
        """Attach a callback for when a node is removed, once per node: by the
        user in the browser (Delete key or context menu), or from Python with
        :meth:`remove_node`. The node is already gone when it runs.

        Note:
            - Removals made from Python run the callback on the calling
              thread, before :meth:`remove_node` returns.
            - Otherwise, if `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._node_remove_cb.append(func)
        return func

    def on_change(
        self, func: Callable[[NodeGraphChangeEvent], NoneOrCoroutine]
    ) -> Callable[[NodeGraphChangeEvent], NoneOrCoroutine]:
        """Attach a callback for when nodes or wires are added, removed or
        moved, or input values edited, by the user in the browser or from
        Python. Each edit is reported once, e.g. a node removed with its
        wires.

        Edits made inside the callback are reported to it again, so it must
        not edit the graph on every call.

        Note:
            - Edits made from Python run the callback on the calling thread,
              before the editing method returns.
            - Otherwise, if `func` is a regular function (defined with `def`), it will be executed in a thread pool.
            - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._change_cb.append(func)
        return func

    def _node_event_callbacks(
        self, kind: Literal["double_click", "edit", "action"], action_id: str
    ) -> list[NodeGraphCallback]:
        if kind == "double_click":
            return list(self._double_click_cb)
        if kind == "edit":
            return list(self._edit_cb)
        action = self._action_handles.get(action_id, None)
        return [] if action is None else list(action._click_cb)

    # Edits made by users in the browser. They race edits from Python, so stale
    # ids are skipped rather than raised. Each returns the change, which the
    # caller reports along with the client that made it.

    def _apply_client_delete(
        self, node_ids: Sequence[str], edge_ids: Sequence[str]
    ) -> _NodeGraphChange | None:
        with self._lock:
            removed = set(node_ids)
            return self._set_graph(
                nodes=tuple(n for n in self.nodes if n.node_id not in removed),
                edges=tuple(
                    e
                    for e in self.edges
                    if e.edge_id not in edge_ids
                    and e.source not in removed
                    and e.target not in removed
                ),
            )

    def _apply_client_move(
        self,
        node_ids: Sequence[str],
        positions: Sequence[Tuple[float, float]],
    ) -> _NodeGraphChange | None:
        with self._lock:
            position_from_id = {
                node_id: (float(x), float(y))
                for node_id, (x, y) in zip(node_ids, positions)
            }
            return self._set_graph(
                nodes=tuple(
                    dataclasses.replace(n, position=position_from_id[n.node_id])
                    if n.node_id in position_from_id
                    else n
                    for n in self.nodes
                )
            )

    def _apply_client_connect(
        self, source: str, source_port: str, target: str, target_port: str
    ) -> _NodeGraphChange | None:
        try:
            return self._connect(source, source_port, target, target_port)[1]
        except (KeyError, ValueError) as e:
            warnings.warn(f"Ignoring invalid wire from the client: {e}")
            return None

    def _apply_client_value(
        self, node_id: str, port: str, value: Any
    ) -> _NodeGraphChange | None:
        try:
            return self._set_value(node_id, port, value)
        except KeyError:
            return None  # The node was removed meanwhile.
        except ValueError as e:
            warnings.warn(f"Ignoring invalid input value from the client: {e}")
            return None


def _find_port(ports: Tuple[GuiNodeGraphPort, ...], name: str) -> GuiNodeGraphPort:
    for port in ports:
        if port.name == name:
            return port
    raise KeyError(f"No port {name!r}; available: {[p.name for p in ports]!r}.")


class _TabContainerMixin:
    """Shared ``add_tab`` machinery for handles that own a tab list.

    A tab container holds the three parallel tab tuples (``_tab_labels`` /
    ``_tab_icons_html`` / ``_tab_container_ids``, provided by the handle's props
    class) plus ``_tab_handles``. Both :class:`GuiTabGroupHandle` (inline tab
    group) and :class:`PanelHandle` (standalone panel) mix this in; the only
    difference is their props class and lifecycle, not how tabs are added. The
    tabs are real GUI containers (each :class:`GuiTabHandle`), so a panel's tabs
    register as dock panes exactly like a tab group's."""

    # Provided by the props class / handle:
    _tab_labels: Tuple[str, ...]
    _tab_icons_html: Tuple[str | None, ...]
    _tab_container_ids: Tuple[str, ...]
    _tab_handles: list[GuiTabHandle]
    _impl: _GuiHandleState[None]

    def add_tab(self, label: str, icon: IconName | None = None) -> GuiTabHandle:
        """Add a tab. Returns a handle we can use to add GUI elements to it."""
        # The removed CHECK, the tab's registration (GuiTabHandle.__post_init__
        # writes the container map), the append, and the props rebuild run as
        # ONE atomic step under the panel lifecycle lock. Two hazards:
        # - Half-registration: guarding after mutating would leak a container
        #   entry when the props write hit the removed-guard and raised.
        # - A CONCURRENT remove() interleaving after a bare check: the remover
        #   tombstones + snapshots an empty tab list, then this append lands
        #   -- registering a live container entry for a panel that no longer
        #   exists, which silently accepts children forever. remove() takes
        #   the same lock for its tombstone, so the pair serializes.
        # Applies to both mixers (tab group and standalone panel).
        with self._impl.gui_api._panel_lifecycle_lock:
            if self._impl.removed:
                raise RuntimeError(
                    f"Cannot add a tab to a removed {type(self).__name__}."
                )

            uuid = _make_uuid()
            out = GuiTabHandle(_parent=self, _id=uuid, _label=label, _icon=icon)
            self._tab_handles.append(out)
            self._rebuild_tab_props()
            return out

    def _rebuild_tab_props(self) -> None:
        """Recompute the three wire tuples from ``_tab_handles`` -- the single
        source of truth for a tab's id/label/icon. Every mutation (add, remove,
        icon change) rebuilds through here, so the parallel tuples can never
        desync in length or order."""
        if self._impl.removed:
            # Tear-down path: both mixers' remove() tombstone BEFORE draining
            # their tabs (the tombstone must be an atomic check-and-set against
            # concurrent removers), so the drain's write-backs land here after
            # removal. Skip the wire write-back: props_setattr would
            # (correctly) reject a props write on a removed handle, and the
            # client drops the whole entity via its remove message anyway.
            return
        self._tab_container_ids = tuple(h._id for h in self._tab_handles)
        self._tab_labels = tuple(h._label for h in self._tab_handles)
        self._tab_icons_html = tuple(
            None if h._icon is None else svg_from_icon(h._icon)
            for h in self._tab_handles
        )


class GuiTabGroupHandle(_TabContainerMixin, _GuiHandle[None], GuiTabGroupProps):
    """Handle for a tab group. Call :meth:`add_tab()` to add a tab."""

    def __init__(self, _impl: _GuiHandleState[None]) -> None:
        super().__init__(impl=_impl)
        self._tab_handles: list[GuiTabHandle] = []

    def __post_init__(self) -> None:
        parent = self._impl.gui_api._resolve_container_handle(
            self._impl.parent_container_id
        )
        parent._children[self._impl.uuid] = self

    def remove(self) -> None:
        """Remove this tab group and all contained GUI elements."""
        gui_api = self._impl.gui_api
        # Tombstone CHECK-AND-SET under the lifecycle lock, mirroring
        # PanelHandle.remove(): add_tab() takes the same lock for its
        # removed-check + append, so an unlocked tombstone here let a
        # concurrent add_tab interleave between our tab snapshot and the
        # tombstone -- registering a live container entry for a group that no
        # longer exists, which silently accepts children forever.
        with gui_api._panel_lifecycle_lock:
            if self._impl.removed:
                warnings.warn(
                    f"Attempted to remove an already removed {self.__class__.__name__}.",
                    stacklevel=2,
                )
                return
            self._impl.removed = True
            gui_api._websock_interface.queue_message(GuiRemoveMessage(self._impl.uuid))
        # Only the tombstone winner reaches here. The tab drain runs AFTER the
        # tombstone and OUTSIDE the (non-reentrant) lock: each tab.remove()
        # writes back to this group's tab tuples, which _rebuild_tab_props
        # skips for a removed group (props_setattr would reject the write; the
        # client drops the whole entity via the remove message anyway).
        for tab in tuple(self._tab_handles):
            _cascade_remove(tab)
        try:
            parent = gui_api._resolve_container_handle(self._impl.parent_container_id)
            parent._children.pop(self._impl.uuid, None)
        except KeyError:
            pass  # Parent already torn down by a disconnect race.
        gui_api._handles_in_foreign_containers.pop(self._impl.uuid, None)


@dataclasses.dataclass
class GuiTabHandle:
    """Use as a context to place GUI elements into a tab."""

    _parent: _TabContainerMixin
    _id: str  # Used as container ID of children.
    _label: str
    _icon: IconName | None
    _container_id_restore: tuple[GuiApi, str] | None = None
    _children: dict[str, SupportsRemoveProtocol] = dataclasses.field(
        default_factory=dict
    )
    removed: bool = False

    @property
    def icon(self) -> IconName | None:
        """Icon to display on the tab. When set to None, no icon is displayed."""
        return self._icon

    @icon.setter
    def icon(self, icon: IconName | None) -> None:
        # The handle owns its icon; rebuild rederives the wire tuples from the
        # handles, so there's no index arithmetic that could target the wrong tab.
        self._icon = icon
        self._parent._rebuild_tab_props()

    def __enter__(self) -> GuiTabHandle:
        if self._container_id_restore is not None:
            # See GuiFolderHandle.__enter__: a single restore slot can't
            # support overlapping `with` blocks on the same handle.
            raise RuntimeError(
                "This GuiTabHandle is already active as a context -- either "
                "nested inside its own `with` block, or entered concurrently "
                "from another thread. A handle supports only one active "
                "`with` block at a time (sequential re-entry is fine)."
            )
        self._container_id_restore = (
            self._parent._impl.gui_api._snapshot_container_context()
        )
        self._parent._impl.gui_api._set_container_uuid(self._id)
        return self

    def __exit__(self, *args) -> None:
        del args
        assert self._container_id_restore is not None
        self._parent._impl.gui_api._restore_container_context(
            self._container_id_restore
        )
        self._container_id_restore = None

    def __post_init__(self) -> None:
        self._parent._impl.gui_api._container_handle_from_uuid[self._id] = self

    def remove(self) -> None:
        """Permanently remove this tab and all contained GUI elements from the
        visualizer."""
        # Warn if already removed.
        if self.removed:
            warnings.warn(
                f"Attempted to remove an already removed {self.__class__.__name__}.",
                stacklevel=2,
            )
            return
        self.removed = True

        # We may want to make this thread-safe in the future.
        assert self in self._parent._tab_handles, "Tab already removed!"
        # Drop this handle; the three wire tuples are rederived from the handle
        # list in one place, so they can't end up mismatched in length or order.
        self._parent._tab_handles = [
            tab for tab in self._parent._tab_handles if tab is not self
        ]
        self._parent._rebuild_tab_props()

        for child in tuple(self._children.values()):
            _cascade_remove(child)
        self._parent._impl.gui_api._container_handle_from_uuid.pop(self._id, None)


# The control panel's fixed uuid, shared with the client (CONTROL_PANEL_ID in
# ControlPanelDock.tsx). Used as the anchor uuid when `main_panel` is a dock
# anchor, and as the placement target for the main panel itself.
CONTROL_PANEL_ID = "viser-control-panel"


def _check_dimension(
    value: float | None, name: str, *, allow_none: bool = True
) -> None:
    """Reject non-positive / non-finite panel sizes before they reach the client.

    NaN/negative/zero widths produce broken (and, for NaN, sticky + replayed)
    layouts; the client's floating-window resize doesn't clamp them, so we
    validate at the Python boundary like other viser inputs do. ``None`` is the
    internal "clear the override" sentinel (used by ``gui.reset()``); public
    commands that require a real size pass ``allow_none=False``."""
    if value is None:
        if not allow_none:
            raise TypeError(f"{name} must be a number in pixels, not None.")
        return
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be a positive, finite number, got {value!r}.")


def _check_coordinate(value: float | None, name: str) -> None:
    """Reject non-finite float coordinates. Unlike dimensions, coordinates may be
    negative (a gap from the far edge) or zero, but NaN/inf would produce a
    broken, sticky, replayed window position."""
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")


_PlacementMessage: TypeAlias = Union[
    GuiSetPanelPositionMessage,
    GuiSetPanelWidthMessage,
    GuiSetPanelHeightMessage,
    GuiSetPanelCollapsedMessage,
]
"""The four per-axis placement messages. All carry `counter` / `run_id` stamp
fields, which `_PlacementMixin._queue_placement` fills in."""


class _PlacementMixin:
    """Shared placement / sizing commands for panel handles.

    Placement is WRITE-ONLY from the server: there is no placement state stored
    or read back here. Each command fires one per-axis message
    (``GuiSetPanel{Position,Width,Height,Collapsed}Message``); the client owns all
    placement state. The messages are ``update_simple`` updates that coalesce
    per-type, persist, and replay to late joiners -- so e.g. ``set_width`` never
    carries a position and cannot re-dock a panel the user has moved.

    Subclasses provide ``_placement_uuid`` (the tab-group uuid to target) and
    ``_placement_gui_api``.
    """

    _placement_uuid: str
    _placement_gui_api: GuiApi

    def _placement_removed(self) -> bool:
        """Whether placement commands must be rejected because the underlying
        entity is gone. This base implementation serves handles that cannot be
        removed (the main panel); `PanelHandle` overrides it."""
        return False

    def _queue_placement(
        self, message: _PlacementMessage, *, anchor: PanelHandle | None = None
    ) -> None:
        api = self._placement_gui_api
        # The removed check and the enqueue are ONE atomic step under the
        # lifecycle lock: a `remove()` on another thread otherwise slips
        # between them -- its purge runs first, then this placement lands in
        # the buffer as immortal residue for a dead uuid (replayed to every
        # late joiner).
        with api._panel_lifecycle_lock:
            # Single guard point for every command: reject placement on a
            # removed panel, which would otherwise queue an update against a
            # dead uuid.
            if self._placement_removed():
                raise RuntimeError(f"Cannot place a removed {type(self).__name__}.")
            # Re-check the anchor (dock_above/dock_below) under the SAME lock:
            # `_resolve_anchor_uuid` runs before this lock is taken, so an
            # `anchor.remove()` on another thread -- whose tombstone is set
            # under this lock; the anchor shares our GuiApi per the scope
            # check -- could otherwise slip between that check and this
            # enqueue, persisting a split placement that references a dead
            # anchor uuid. Same error the sequential path promises.
            if anchor is not None and anchor._impl.removed:
                raise ValueError("Cannot dock relative to a removed panel.")
            # Stamp the layout-update counter (bumped on EVERY placement
            # command, global across panels -- D50: conflicting container-
            # scoped collapse axes replay in counter order) and this GuiApi's
            # run id. The client uses the pair to ignore replayed/late
            # placement for a panel the user has since rearranged (see the
            # message's `counter` / `run_id` docs). Methods construct the
            # message with placeholder counter=0 / run_id=""; the
            # authoritative values are assigned here, the single chokepoint.
            stamped = dataclasses.replace(
                message,
                counter=api._next_layout_counter(),
                run_id=api._layout_run_id,
            )
            api._websock_interface.queue_message(stamped)

    def _set_position(
        self,
        position: EdgePlacement | SplitPlacement | FloatPlacement,
        *,
        anchor: PanelHandle | None = None,
    ) -> None:
        self._queue_placement(
            GuiSetPanelPositionMessage(
                self._placement_uuid, position, counter=0, run_id=""
            ),
            anchor=anchor,
        )

    def _resolve_anchor_uuid(self, anchor: PlaceableHandle) -> str:
        """Validate an anchor and return its tab-group uuid.

        The anchor must share this panel's scope, except `main_panel`, which
        renders on every client and is a legal anchor from any scope."""
        if isinstance(anchor, MainPanelHandle):
            # main_panel: legal from any scope, no removed/scope checks. Still
            # reject docking it relative to itself (uuid check, not identity --
            # `main_panel` hands out a fresh throwaway handle per access).
            if self._placement_uuid == CONTROL_PANEL_ID:
                raise ValueError("A panel cannot be docked relative to itself.")
            return CONTROL_PANEL_ID
        if not isinstance(anchor, PanelHandle):
            # User-facing API: a clear error, not an AssertionError (which `-O`
            # would strip, letting a wrong-typed anchor slip through).
            raise ValueError(
                "Anchor must be a PanelHandle or main_panel, got "
                f"{type(anchor).__name__}."
            )
        if anchor._impl.uuid == self._placement_uuid:
            raise ValueError("A panel cannot be docked relative to itself.")
        if anchor._impl.removed:
            raise ValueError("Cannot dock relative to a removed panel.")
        if anchor._impl.gui_api is not self._placement_gui_api:
            raise ValueError(
                "Anchor panel belongs to a different scope (server vs. client, "
                "or a different client). Use `main_panel` to anchor across scopes."
            )
        return anchor._impl.uuid

    def dock_left(self) -> None:
        """Dock this panel to the left viewport edge.

        Each new left-dock is inserted at the innermost position, so several
        panels docked to the left appear in call order from the edge inward.
        Calling again repositions an already-placed panel."""
        self._set_position({"kind": "edge", "edge": "left"})

    def dock_right(self) -> None:
        """Dock this panel to the right viewport edge.

        Each new right-dock is inserted at the innermost position, so several
        panels docked to the right appear in call order from the edge inward.
        Calling again repositions an already-placed panel."""
        self._set_position({"kind": "edge", "edge": "right"})

    def dock_above(self, anchor: PlaceableHandle) -> None:
        """Stack this panel directly above another panel (a column split).

        The ``anchor`` must itself be DOCKED (a column split needs a docked
        neighbor to split against). If the anchor is floating or not yet placed,
        this falls back to docking on the right edge (with a warning); dock the
        anchor first."""
        self._set_position(
            {
                "kind": "split",
                "anchor_uuid": self._resolve_anchor_uuid(anchor),
                "side": "above",
            },
            # Re-validated under the lifecycle lock (see _queue_placement);
            # main_panel needs no removed-check (it cannot be removed).
            anchor=anchor if isinstance(anchor, PanelHandle) else None,
        )

    def dock_below(self, anchor: PlaceableHandle) -> None:
        """Stack this panel directly below another panel (a column split).

        The ``anchor`` must itself be DOCKED (a column split needs a docked
        neighbor to split against). If the anchor is floating or not yet placed,
        this falls back to docking on the right edge (with a warning); dock the
        anchor first."""
        self._set_position(
            {
                "kind": "split",
                "anchor_uuid": self._resolve_anchor_uuid(anchor),
                "side": "below",
            },
            # Re-validated under the lifecycle lock (see _queue_placement);
            # main_panel needs no removed-check (it cannot be removed).
            anchor=anchor if isinstance(anchor, PanelHandle) else None,
        )

    def float(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
    ) -> None:
        """Float this panel at an explicit position and size, in CSS pixels.

        ``x`` / ``y`` are measured relative to the **viewport** -- the canvas area
        inside any docked panels:

        * A non-negative value is a gap from the **left** / **top**:
          ``float(x=40)`` lands 40px from the canvas left edge (clear of a
          left-docked panel, not under it).
        * A **negative** value is a gap from the **right** / **bottom**:
          ``float(x=-15)`` puts the panel's right edge 15px from the canvas right
          edge. So ``float(x=-15, y=15)`` is the top-right corner, and
          ``float(x=-15, y=-15)`` the bottom-right.

        The panel re-resolves against these edges as the canvas changes (a dock
        added/removed, the window resized), so an edge-anchored panel stays put.
        Any argument left as ``None`` uses a client-chosen default (top-left);
        ``width`` / ``height`` set the floating window size."""
        _check_coordinate(x, "x")
        _check_coordinate(y, "y")
        _check_dimension(width, "width")
        _check_dimension(height, "height")
        self._set_position({"kind": "float", "x": x, "y": y})
        if width is not None:
            self.set_width(width)
        if height is not None:
            self.set_height(height)

    def set_width(self, width: float) -> None:
        """Set the panel width in pixels (region width when docked, window width
        when floating)."""
        _check_dimension(width, "width", allow_none=False)
        self._queue_placement(
            GuiSetPanelWidthMessage(self._placement_uuid, width, counter=0, run_id="")
        )

    def set_height(self, height: float) -> None:
        """Set the panel height in pixels.

        Applies only to **floating** panels (sets the window height). A docked
        panel -- whether solo or stacked via :meth:`dock_above` /
        :meth:`dock_below` -- sizes to its split weights, so ``set_height`` has no
        effect there."""
        _check_dimension(height, "height", allow_none=False)
        self._queue_placement(
            GuiSetPanelHeightMessage(self._placement_uuid, height, counter=0, run_id="")
        )

    def minimize(self) -> None:
        """Minimize the panel (collapse it to a bar / rail strip).

        Collapse is applied to the panel's *container* -- its floating window
        or its docked column -- so panels stacked together minimize together,
        exactly like the on-screen minimize control. Imperative, like the
        ``dock_*`` / :meth:`float` commands: it always minimizes, even if the
        user expanded the panel in the browser. Replayed to clients that
        connect later. On the mobile bottom sheet (where panels render as
        sections, not windows) this collapses the panel's section.

        TODO: add a matching imperative collapse/expand method for folders
        (:meth:`GuiApi.add_folder`), which today only has the
        ``expand_by_default`` creation kwarg.
        """
        self._queue_placement(
            GuiSetPanelCollapsedMessage(
                self._placement_uuid, True, counter=0, run_id=""
            )
        )

    def expand(self) -> None:
        """Expand (un-minimize) the panel, the inverse of :meth:`minimize`.

        Like :meth:`minimize` this acts at container scope -- expanding a
        panel reveals its whole stack (and clears a docked column's rail) --
        and is imperative: it always expands, even if the user minimized the
        panel in the browser. Replayed to clients that connect later."""
        self._queue_placement(
            GuiSetPanelCollapsedMessage(
                self._placement_uuid, False, counter=0, run_id=""
            )
        )


class PanelHandle(
    _PlacementMixin,
    _TabContainerMixin,
    AssignablePropsBase[_GuiHandleState],
    GuiPanelProps,
):
    """Handle for a standalone panel: a dockable / floating GUI container that
    lives outside the main control panel.

    Create with :meth:`GuiApi.add_panel`. Add content with :meth:`add_tab`, and
    place it with the imperative ``dock_*`` / :meth:`float` commands. A panel is a
    dedicated top-level entity (like a modal) -- it is NOT part of the inline GUI
    tree -- that carries its own tabs; a single-tab panel renders as a plain
    header.

    Placement, sizing, and collapse are **imperative commands, not synced
    state**: they apply to connected clients and replay to clients that connect
    later, but the current layout is never read back from clients. There are no
    readable ``.width`` / position / minimized properties, and a user dragging
    or minimizing the panel afterward wins until the next explicit command.
    (This is why sizing is ``set_width()`` rather than a ``.width`` property.)
    :meth:`minimize` / :meth:`expand` act on the panel's *container* -- its
    floating window or its docked column -- so panels stacked together
    minimize together, exactly like the on-screen minimize control.

    The server owns a panel's existence: users can rearrange, drag, minimize, and
    resize a panel, but cannot close it from the UI. A panel disappears only when
    :meth:`remove` is called.

    .. note::
        Panels are a new API surface; method names and placement semantics may
        still evolve in upcoming releases."""

    def __init__(self, _impl: _GuiHandleState[None]) -> None:
        super().__init__(impl=_impl)
        self._tab_handles: list[GuiTabHandle] = []
        self._placement_uuid = _impl.uuid
        self._placement_gui_api = _impl.gui_api
        assert isinstance(_impl.props, GuiPanelProps)
        # A panel is a top-level entity tracked in its own registry (parallel to
        # modals), NOT under any parent container's `_children`. Its TABS register
        # themselves in `_container_handle_from_uuid` (keyed by tab id), so the
        # panel itself doesn't need a container entry.
        _impl.gui_api._panel_handle_from_uuid[_impl.uuid] = self

    @override
    def _placement_removed(self) -> bool:
        return self._impl.removed

    @override
    def _queue_update(self, name: str, value: Any) -> None:
        self._impl.gui_api._websock_interface.queue_message(
            GuiUpdateMessage(self._impl.uuid, {name: value})
        )

    def add_tab(self, label: str, icon: IconName | None = None) -> GuiTabHandle:
        """Add a tab to the panel, returning a handle to populate it as a context.

        A single-tab panel renders as a plain header; multiple tabs render as a
        tab strip. Raises if the panel has been removed (the shared
        :class:`_TabContainerMixin` guard)."""
        return super().add_tab(label, icon)

    def __enter__(self) -> "PanelHandle":
        # A panel is a container for TABS, not a GUI context itself: you populate
        # its tabs (`with panel.add_tab(...):`), not the panel. Catch the natural
        # `with server.gui.add_panel():` mistake with a clear message instead of a
        # bare `AttributeError: __exit__`. Both `__enter__` AND `__exit__` must be
        # defined: CPython's `with` looks up `__exit__` on the type *before*
        # calling `__enter__`, so omitting it surfaces `AttributeError: __exit__`
        # and this helpful message never runs.
        raise TypeError(
            "A panel is not a context manager. Add content via its tabs, e.g.\n"
            "    panel = server.gui.add_panel()\n"
            '    with panel.add_tab("Tab"):\n'
            "        server.gui.add_markdown(...)"
        )

    def __exit__(self, *args) -> None:
        # Never reached (`__enter__` always raises), but must exist so the `with`
        # statement's pre-flight `__exit__` lookup finds it and lets `__enter__`
        # raise the helpful TypeError above.
        del args

    def remove(self) -> None:
        """Permanently remove this panel and all its tabs / contained elements.

        This is the only way a panel disappears -- there is no UI close button
        (see :class:`PanelHandle`)."""
        gui_api = self._impl.gui_api
        # Tombstone CHECK-AND-SET + remove-message (whose buffer push purges
        # the panel's placement updates) as ONE atomic step under the
        # lifecycle lock. This serves two races:
        # - a concurrent placement command can't slip between the removed
        #   check and its enqueue (see _queue_placement);
        # - two concurrent remove() calls (or remove() racing gui.reset()'s
        #   drain) resolve to exactly one winner -- checking `removed` before
        #   taking the lock let both pass, queue duplicate remove messages,
        #   and double-pop the registry (KeyError, aborting reset mid-drain).
        with gui_api._panel_lifecycle_lock:
            if self._impl.removed:
                warnings.warn(
                    "Attempted to remove an already removed PanelHandle.",
                    stacklevel=2,
                )
                return
            self._impl.removed = True
            gui_api._websock_interface.queue_message(
                GuiPanelRemoveMessage(self._impl.uuid)
            )
        # Only the tombstone winner reaches here, so the pop and tab drain run
        # exactly once -- a strict pop() raises if that invariant ever breaks,
        # which beats hiding the breakage. The drain runs AFTER the tombstone
        # and OUTSIDE the lock (a plain, non-reentrant Lock): each
        # tab.remove() writes back to this panel's tab tuples, which
        # _rebuild_tab_props skips for a removed panel (props_setattr would
        # reject the write; the client drops the whole entity via the remove
        # message anyway).
        gui_api._panel_handle_from_uuid.pop(self._impl.uuid)
        for tab in tuple(self._tab_handles):
            _cascade_remove(tab)


class MainPanelHandle(_PlacementMixin):
    """Handle for the main control panel. Returned by :attr:`GuiApi.main_panel`.

    Supports the same placement / sizing / minimize commands as
    :class:`PanelHandle`, but nothing else from it: the main panel has no tabs
    (its content is the inline GUI tree), no ``visible`` property, and cannot
    be removed. Because the control panel renders on every client, it is a
    legal anchor for other panels' ``dock_*`` commands from any scope.

    A fresh handle is returned on each access; placement state is keyed off the
    control panel's fixed uuid, so handles are interchangeable.

    .. note::
        Panels are a new API surface; method names and placement semantics may
        still evolve in upcoming releases."""

    def __init__(self, gui_api: GuiApi) -> None:
        # Placement is write-only (per-axis messages keyed by CONTROL_PANEL_ID);
        # no state to hold, so the throwaway handles `main_panel` returns are all
        # equivalent.
        self._placement_uuid = CONTROL_PANEL_ID
        self._placement_gui_api = gui_api


PlaceableHandle: TypeAlias = "PanelHandle | MainPanelHandle"
"""A handle that can be used as a dock anchor: a standalone panel or the main
panel."""


class GuiFolderHandle(_GuiHandle[None], GuiFolderProps):
    """Use as a context to place GUI elements into a folder."""

    def __init__(self, _impl: _GuiHandleState[None]) -> None:
        super().__init__(impl=_impl)
        self._impl.gui_api._container_handle_from_uuid[self._impl.uuid] = self
        self._children = {}
        parent = self._impl.gui_api._resolve_container_handle(
            self._impl.parent_container_id
        )
        parent._children[self._impl.uuid] = self

    _container_id_restore: tuple[GuiApi, str] | None = None

    def __enter__(self) -> Self:
        if self._container_id_restore is not None:
            # A single restore slot can't support overlapping `with` blocks on
            # the SAME handle: self-nesting would strand the container pointer
            # inside this folder, silently misplacing every later element, and
            # a concurrent enter from another thread would corrupt the other
            # block's restore. (Sequential re-entry -- `with f: ...` twice --
            # is fine; __exit__ clears the slot.)
            raise RuntimeError(
                "This GuiFolderHandle is already active as a context -- either "
                "nested inside its own `with` block, or entered concurrently "
                "from another thread. A handle supports only one active "
                "`with` block at a time (sequential re-entry is fine)."
            )
        self._container_id_restore = self._impl.gui_api._snapshot_container_context()
        self._impl.gui_api._set_container_uuid(self._impl.uuid)
        return self

    def __exit__(self, *args) -> None:
        del args
        assert self._container_id_restore is not None
        self._impl.gui_api._restore_container_context(self._container_id_restore)
        self._container_id_restore = None

    def remove(self) -> None:
        """Permanently remove this folder and all contained GUI elements from the
        visualizer."""
        # Warn if already removed.
        if self._impl.removed:
            warnings.warn(
                f"Attempted to remove an already removed {self.__class__.__name__}.",
                stacklevel=2,
            )
            return
        self._impl.removed = True

        # Remove children, then self.
        gui_api = self._impl.gui_api
        gui_api._websock_interface.queue_message(GuiRemoveMessage(self._impl.uuid))
        for child in tuple(self._children.values()):
            _cascade_remove(child)
        try:
            parent = gui_api._resolve_container_handle(self._impl.parent_container_id)
            parent._children.pop(self._impl.uuid, None)
        except KeyError:
            pass  # Parent already torn down by a disconnect race.
        gui_api._container_handle_from_uuid.pop(self._impl.uuid, None)
        gui_api._handles_in_foreign_containers.pop(self._impl.uuid, None)


class GuiFormHandle(GuiFolderHandle):
    """Use as a context to place GUI elements into a form.

    A form is a folder whose children's values can be committed together by
    calling :meth:`submit` (typically from a button's ``on_click`` handler) or
    by pressing Enter in a single-line text input inside the form.

    Children of a form behave exactly like children of a folder. ``on_update``
    callbacks on individual inputs continue to fire on every keystroke; the
    form's :meth:`on_submit` callback fires only when the form is submitted.
    Register one or both depending on whether you want live or commit
    semantics.

    The form's client-side dirty indicator highlights when any descendant
    input has been edited since the last submit.

    Forms cannot be nested. Calling :meth:`GuiApi.add_form` inside an
    existing form's context will raise :class:`ValueError`, because nested
    ``<form>`` elements are invalid HTML on the client.

    Example::

        with server.gui.add_form("Profile") as form:
            name = server.gui.add_text("Name", "")
            age = server.gui.add_number("Age", 0)
            save = server.gui.add_button("Save")

        save.on_click(lambda _: form.submit())

        @form.on_submit
        def _(event):
            print(name.value, age.value)
    """

    def __init__(self, _impl: _GuiHandleState[None]) -> None:
        super().__init__(_impl)
        self._submit_cb: list[
            Callable[[GuiEvent[GuiFormHandle]], None | Coroutine]
        ] = []

    def on_submit(
        self,
        func: Callable[[GuiEvent[GuiFormHandle]], NoneOrCoroutine],
    ) -> Callable[[GuiEvent[GuiFormHandle]], NoneOrCoroutine]:
        """Attach a function to call when the form is submitted.

        ``on_submit`` is independent from ``on_update`` callbacks on child
        inputs: child ``on_update`` callbacks fire on every keystroke (as
        normal), and the form's ``on_submit`` fires when commit happens (via
        ``form.submit()`` or Enter in a single-line text input).

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.
        """
        self._submit_cb.append(func)
        return func

    def remove_submit_callback(
        self, callback: Literal["all"] | Callable = "all"
    ) -> None:
        """Remove submit callbacks from the form.

        Args:
            callback: Either "all" to remove all callbacks, or a specific callback function to remove.
        """
        if callback == "all":
            self._submit_cb.clear()
        else:
            self._submit_cb = [cb for cb in self._submit_cb if cb != callback]

    def submit(self) -> None:
        """Programmatically submit this form.

        Fires all registered ``on_submit`` callbacks and broadcasts a
        :class:`GuiFormSubmitMessage` to all clients so their dirty indicators
        are cleared.
        """
        gui_api = self._impl.gui_api
        # Fire on_submit callbacks. Server-initiated submits have no client.
        for cb in self._submit_cb:
            cb_out = cb(GuiEvent(client_id=None, client=None, target=self))
            if isinstance(cb_out, Coroutine):
                # run_coroutine_threadsafe, not create_task: submit() is
                # typically called from a user thread (e.g. a button's
                # on_click handler), and create_task is neither thread-safe
                # nor guaranteed to wake the loop.
                asyncio.run_coroutine_threadsafe(
                    cb_out, gui_api._event_loop
                ).add_done_callback(print_task_error)
        # Broadcast to clients so they reset dirty state.
        gui_api._websock_interface.queue_message(
            GuiFormSubmitMessage(uuid=self._impl.uuid)
        )


@dataclasses.dataclass
class GuiModalHandle:
    """Use as a context to place GUI elements into a modal."""

    _gui_api: GuiApi
    _uuid: str  # Used as container ID of children.
    _container_uuid_restore: tuple[GuiApi, str] | None = None
    _children: dict[str, SupportsRemoveProtocol] = dataclasses.field(
        default_factory=dict
    )
    closed: bool = False

    def __enter__(self) -> GuiModalHandle:
        self._container_uuid_restore = self._gui_api._snapshot_container_context()
        self._gui_api._set_container_uuid(self._uuid)
        return self

    def __exit__(self, *args) -> None:
        del args
        assert self._container_uuid_restore is not None
        self._gui_api._restore_container_context(self._container_uuid_restore)
        self._container_uuid_restore = None

    def __post_init__(self) -> None:
        self._gui_api._container_handle_from_uuid[self._uuid] = self
        self._gui_api._modal_handle_from_uuid[self._uuid] = self

    def close(self) -> None:
        """Close this modal and permananently remove all contained GUI elements."""
        if self.closed:
            warnings.warn(
                "Attempted to close an already closed GuiModalHandle.",
                stacklevel=2,
            )
            return
        self.closed = True
        self._gui_api._websock_interface.queue_message(
            GuiCloseModalMessage(self._uuid),
        )
        for child in tuple(self._children.values()):
            _cascade_remove(child)
        self._gui_api._container_handle_from_uuid.pop(self._uuid)
        self._gui_api._modal_handle_from_uuid.pop(self._uuid)


@dataclasses.dataclass
class TimelineHandle:
    """Handle for timeline slider widget.

    Provides access to timeline state and callbacks for user interactions.
    """

    _gui_api: GuiApi
    _value: float
    _props: TimelineProps
    _update_callbacks: list[Callable[[GuiEvent], None | Coroutine]]
    _play_callbacks: list[Callable[[GuiEvent], None | Coroutine]]
    _removed: bool = False
    _playing: bool = False

    @property
    def value(self) -> float:
        """Current slider value. Synchronized automatically when assigned."""
        return self._value

    @value.setter
    def value(self, value: float) -> None:
        """Update slider value and sync to all clients."""
        self._value = value
        self._gui_api._websock_interface.queue_message(
            GuiUpdateMessage("__timeline__", {"value": value})
        )

    @property
    def playing(self) -> bool:
        """Whether the play button is currently in its "playing" state.

        Updated from the client each time the play/pause button is toggled,
        before :meth:`on_play` callbacks run -- so a callback can read this to
        tell a play click from a pause click.
        """
        return self._playing

    @property
    def visible(self) -> bool:
        """Visibility state. Synchronized automatically when assigned."""
        return self._props.visible

    @visible.setter
    def visible(self, visible: bool) -> None:
        """Update visibility and sync to all clients."""
        self._props.visible = visible
        self._gui_api._websock_interface.queue_message(
            GuiUpdateMessage("__timeline__", {"visible": visible})
        )

    def on_update(
        self, func: Callable[[GuiEvent], None | Coroutine]
    ) -> Callable[[GuiEvent], None | Coroutine]:
        """Attach callback for when slider value changes.

        Args:
            func: Callback function. Takes GuiEvent with client info.

        Returns:
            The input function (for use as decorator).
        """
        self._update_callbacks.append(func)
        return func

    def on_play(
        self, func: Callable[[GuiEvent], None | Coroutine]
    ) -> Callable[[GuiEvent], None | Coroutine]:
        """Attach callback for when the play/pause button is clicked.

        Fires on both play and pause clicks; read :attr:`playing` inside the
        callback to tell them apart.

        Args:
            func: Callback function. Takes GuiEvent with client info.

        Returns:
            The input function (for use as decorator).
        """
        self._play_callbacks.append(func)
        return func

    def remove(self) -> None:
        """Remove timeline from display."""
        if self._removed:
            return
        self._removed = True
        self._gui_api._websock_interface.queue_message(TimelineRemoveMessage())
        self._gui_api._timeline_handle = None


def _get_data_url(url: str, image_root: Path | None) -> str:
    if not url.startswith("http") and not image_root:
        warnings.warn(
            (
                "No `image_root` provided. All relative paths will be scoped to viser's"
                " installation path."
            ),
            stacklevel=2,
        )
    if url.startswith("http") or url.startswith("data:"):
        return url
    if image_root is None:
        image_root = Path(__file__).parent
    try:
        import imageio.v3 as iio

        image = iio.imread(image_root / url)
        _, binary = _encode_image_binary(image, "png")
        url = base64.b64encode(binary).decode("utf-8")
        return f"data:image/png;base64,{url}"
    except (IOError, FileNotFoundError):
        warnings.warn(
            f"Failed to read image {url}, with image_root set to {image_root}.",
            stacklevel=2,
        )
        return url


def _parse_markdown(markdown: str, image_root: Path | None) -> str:
    markdown = re.sub(
        r"\!\[([^]]*)\]\(([^]]*)\)",
        lambda match: (
            f"![{match.group(1)}]({_get_data_url(match.group(2), image_root)})"
        ),
        markdown,
    )
    return markdown


class GuiProgressBarHandle(_GuiInputHandle[float], GuiProgressBarProps):
    """Handle for updating and removing progress bars."""


class GuiMarkdownHandle(_GuiHandle[None], GuiMarkdownProps):
    """Handling for updating and removing markdown elements."""

    def __init__(self, _impl: _GuiHandleState, _content: str, _image_root: Path | None):
        super().__init__(impl=_impl)
        self._content = _content
        self._image_root = _image_root

    @property
    def content(self) -> str:
        """Current content of this markdown element. Synchronized automatically when assigned."""
        assert self._content is not None
        return self._content

    @content.setter
    def content(self, content: str) -> None:
        self._content = content
        self._markdown = _parse_markdown(content, self._image_root)


class GuiHtmlHandle(_GuiHandle[None], GuiHtmlProps):
    """Handling for updating and removing HTML elements."""


class GuiColorbarHandle(_GuiHandle[None], GuiColorbarProps):
    """Handle for updating and removing colorbars.

    Every prop is assignable, so a colorbar can follow a live colormap or data
    range: ``colorbar.colors = new_ramp``, ``colorbar.vmax = 42.0``.
    """


class GuiDividerHandle(_GuiHandle[None], GuiDividerProps):
    """Handle for updating and removing dividers."""


class GuiPlotlyHandle(_GuiHandle[None], GuiPlotlyProps):
    """Handle for updating and removing Plotly figures."""

    def __init__(
        self,
        _impl: _GuiHandleState,
        _figure: go.Figure,
        _config: Mapping[str, Any] | None = None,
    ):
        super().__init__(impl=_impl)
        self._figure = _figure
        self._config = _config

    @property
    def figure(self) -> go.Figure:
        """Current Plotly figure. Synchronized automatically when assigned."""
        assert self._figure is not None
        return self._figure

    @figure.setter
    def figure(self, figure: go.Figure) -> None:
        self._figure = figure
        json_str = figure.to_json()
        assert isinstance(json_str, str)
        if self._config is not None:
            plot_dict = json.loads(json_str)
            plot_dict["config"] = {**plot_dict.get("config", {}), **self._config}
            json_str = json.dumps(plot_dict)
        self._plotly_json_str = json_str


class GuiUplotHandle(_GuiHandle[None], GuiUplotProps):
    """Handle for updating and removing Uplot figures."""

    pass


class GuiImageHandle(_GuiHandle[None], GuiImageProps):
    """Handle for updating and removing images."""

    def __init__(
        self,
        _impl: _GuiHandleState,
        _image: np.ndarray,
        _jpeg_quality: int | None,
    ):
        super().__init__(impl=_impl)
        self._image = _image
        self._jpeg_quality = _jpeg_quality
        self._user_format: Literal["auto", "jpeg", "png"] = (
            "auto"  # Default if not set.
        )

    @property
    def image(self) -> np.ndarray:
        """Current content of this image element. Synchronized automatically when assigned."""
        assert self._image is not None
        return self._image

    @image.setter
    def image(self, image: np.ndarray) -> None:
        self._image = image
        resolved_format, data = _encode_image_binary(
            image, self._user_format, jpeg_quality=self._jpeg_quality
        )
        self._format = resolved_format
        self._data = data

    @property
    def format(self) -> Literal["auto", "jpeg", "png"]:
        """Image format. 'auto' will use PNG for RGBA images and JPEG for RGB."""
        return self._user_format

    @format.setter
    def format(self, value: Literal["auto", "jpeg", "png"]) -> None:
        # Skip if format isn't changing.
        if self._user_format == value:
            return

        self._user_format = value

        # Re-encode image.
        if value == "jpeg" and self._image.shape[2] == 4:
            warnings.warn(
                "Converting RGBA image to JPEG will discard the alpha channel."
            )
        resolved_format, data = _encode_image_binary(
            self._image, value, jpeg_quality=self._jpeg_quality
        )
        self._format = resolved_format
        self._data = data


@dataclasses.dataclass(frozen=True)
class CommandEvent:
    """Information associated with a command trigger from the command palette.

    Passed as input to callback functions.

    ``client`` and ``client_id`` are typed Optional for parity with
    :class:`GuiEvent` (which can fire server-side) and to leave room for a
    future programmatic ``handle.trigger()`` path. In practice, every command
    trigger today originates from a real client -- the dispatcher drops the
    event if the client can't be resolved, so callbacks only see non-None
    values."""

    client: ClientHandle | None
    """Client that triggered this command."""
    client_id: int | None
    """ID of client that triggered this command."""
    target: CommandHandle
    """Command handle that was triggered."""


@dataclasses.dataclass
class _CommandHandleState:
    """Internal state for a registered command."""

    uuid: str
    gui_api: GuiApi
    props: CommandProps
    icon: IconName | None
    trigger_cb: list[Callable[[CommandEvent], None | Coroutine]] = dataclasses.field(
        default_factory=list
    )
    removed: bool = False


class CommandHandle(AssignablePropsBase[_CommandHandleState], CommandProps):
    """Handle for a command registered in the command palette.

    Commands are shown in a command palette (Ctrl/Cmd+K, also Ctrl/Cmd+Shift+P
    on non-Firefox browsers) and can optionally be triggered via hotkeys.

    (Experimental) The command palette API may change in future releases."""

    def __init__(self, _impl: _CommandHandleState) -> None:
        super().__init__(impl=_impl)

    @property
    def icon(self) -> IconName | None:
        """Icon displayed in the command palette."""
        return self._impl.icon

    @icon.setter
    def icon(self, icon: IconName | None) -> None:
        # Removed-guard enforced upstream by AssignablePropsBase.__setattr__.
        self._impl.icon = icon
        self._impl.props._icon_html = None if icon is None else svg_from_icon(icon)
        self._queue_update("_icon_html", self._impl.props._icon_html)

    def _queue_update(self, name: str, value: Any) -> None:
        self._impl.gui_api._websock_interface.queue_message(
            CommandUpdateMessage(uuid=self._impl.uuid, updates={name: value})
        )

    def on_trigger(
        self, func: Callable[[CommandEvent], NoneOrCoroutine]
    ) -> Callable[[CommandEvent], NoneOrCoroutine]:
        """Attach a function to call when this command is triggered.

        Note:
        - If `func` is a regular function (defined with `def`), it will be executed in a thread pool.
        - If `func` is an async function (defined with `async def`), it will be executed in the event loop.

        Using async functions can be useful for reducing race conditions.
        """
        if self._impl.removed:
            raise RuntimeError(
                "Cannot attach a trigger callback to a removed CommandHandle."
            )
        self._impl.trigger_cb.append(func)
        return func

    def remove(self) -> None:
        """Remove this command from the command palette."""
        if self._impl.removed:
            warnings.warn(
                "Attempted to remove an already removed CommandHandle.",
                stacklevel=2,
            )
            return
        self._impl.removed = True
        gui_api = self._impl.gui_api
        gui_api._websock_interface.queue_message(RemoveCommandMessage(self._impl.uuid))
        gui_api._command_handle_from_uuid.pop(self._impl.uuid, None)
