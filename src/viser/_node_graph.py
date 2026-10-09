"""Public types for the node graph GUI component."""

from __future__ import annotations

import dataclasses


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


def dtypes_compatible(output_dtype: str, input_dtype: str) -> bool:
    """Whether an output port of ``output_dtype`` can feed an input port of
    ``input_dtype``. Mirrored by the client's connection check."""
    return output_dtype == input_dtype or "any" in (output_dtype, input_dtype)
