"""pbconvert - convert protobuf messages between binary wire format and text format."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .core import (
    ConversionError,
    all_messages,
    convert,
    detect_format,
    load_proto,
    message_class,
    parse_binary,
    parse_text,
    pick_message,
    serialize,
)

try:
    __version__ = version("pbconvert")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"

__all__ = [
    "ConversionError",
    "__version__",
    "all_messages",
    "convert",
    "detect_format",
    "load_proto",
    "message_class",
    "parse_binary",
    "parse_text",
    "pick_message",
    "serialize",
]
