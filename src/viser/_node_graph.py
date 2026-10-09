"""Public types for the node graph GUI component."""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Literal


@dataclasses.dataclass(frozen=True)
class NodePort:
    """An input or output port of a node type.

    Wires can only join an output to an input of the same ``dtype``; a port
    with dtype ``"any"`` accepts every other dtype.
    """

    name: str
    """Identifier of the port, unique among the node type's inputs (or
    outputs)."""
    dtype: str = "any"
    """Data type carried by the port."""
    label: str | None = None
    """Text displayed next to the port. Defaults to ``name``."""
    default: bool | int | float | str | None = None
    """Inputs only: initial value of an input the user can edit in the node
    while no wire is plugged into it. Its type picks the field: a checkbox
    for ``bool``, a number field for ``int`` or ``float``, a text field for
    ``str``. None for an input that only takes wires."""


def value_kind(
    value: bool | int | float | str,
) -> Literal["bool", "int", "float", "str"]:
    """Kind of field used to edit ``value``."""
    # bool first: it is a subclass of int.
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    raise TypeError(
        f"Unsupported input value {value!r}: use a bool, int, float or str."
    )


def coerce_value(
    kind: Literal["bool", "int", "float", "str"], value: Any
) -> bool | int | float | str:
    """Checks that ``value`` fits an input of ``kind``, converting integral
    numbers between int and float."""
    if kind == "bool" and isinstance(value, bool):
        return value
    if kind == "str" and isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if kind == "float" and math.isfinite(value):
            return float(value)
        if kind == "int" and float(value).is_integer():
            return int(value)
    raise ValueError(f"Expected a {kind} value, got {value!r}.")


def dtypes_compatible(output_dtype: str, input_dtype: str) -> bool:
    """Whether an output port of ``output_dtype`` can feed an input port of
    ``input_dtype``. Mirrored by the client's connection check."""
    return output_dtype == input_dtype or "any" in (output_dtype, input_dtype)
