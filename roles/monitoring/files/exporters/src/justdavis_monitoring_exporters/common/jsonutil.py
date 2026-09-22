"""Typed narrowing helpers for `json.loads` output.

`json.loads` returns `Any`; these helpers turn each access into a checked step that raises
`ParseError` naming the context, so a device firmware change fails loudly at the exact path.
"""

from collections.abc import Mapping, Sequence

from justdavis_monitoring_exporters.common.errors import ParseError


def as_dict(value: object, ctx: str) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    raise ParseError(f"{ctx}: expected mapping, got {type(value).__name__}")


def as_list(value: object, ctx: str) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    raise ParseError(f"{ctx}: expected list, got {type(value).__name__}")


def as_str(value: object, ctx: str) -> str:
    if isinstance(value, str):
        return value
    raise ParseError(f"{ctx}: expected string, got {type(value).__name__}")


def as_int(value: object, ctx: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ParseError(f"{ctx}: expected integer, got {type(value).__name__}")


def as_bool(value: object, ctx: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ParseError(f"{ctx}: expected boolean, got {type(value).__name__}")


def get(mapping: Mapping[str, object], key: str, ctx: str) -> object:
    """Return `mapping[key]`, raising `ParseError` naming `ctx.key` when it is absent."""
    if key in mapping:
        return mapping[key]
    raise ParseError(f"{ctx}.{key}: missing")
