"""Core conversion logic: compile a .proto, pick a message type, parse and serialize.

Binary protobuf has no field names, so a schema (.proto file) and the message
type are required in both directions.
"""

from __future__ import annotations

import importlib.resources as ir
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TextIO

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, text_format
from google.protobuf.message import DecodeError
from grpc_tools import protoc

TEXT_SUFFIXES = frozenset({".txtpb", ".textproto", ".pbtxt", ".txt", ".prototxt"})
BINARY_SUFFIXES = frozenset({".pb", ".bin", ".binpb"})
FORMATS = ("binary", "text")


class ConversionError(Exception):
    """A user-facing error: bad schema, unknown message, unparsable input, ..."""


# --------------------------------------------------------------------------- schema


def load_proto(proto_file: str | Path, includes: Iterable[str | Path] = ()):
    """Compile ``proto_file`` (in-process, via grpcio-tools) into a private pool.

    Returns ``(pool, file_descriptor, file_names)`` where ``file_names`` lists every
    compiled file (imports first, the requested file last).
    """
    proto_path = Path(proto_file).resolve()
    if not proto_path.is_file():
        raise ConversionError(f"Proto file not found: {proto_file}")
    wkt_dir = str(ir.files("grpc_tools") / "_proto")  # google/protobuf/*.proto

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "desc.bin"
        args = [
            "protoc",
            f"-I{proto_path.parent}",
            *[f"-I{i}" for i in includes],
            f"-I{wkt_dir}",
            f"--descriptor_set_out={out}",
            "--include_imports",
            str(proto_path),
        ]
        if protoc.main(args) != 0:
            raise ConversionError("protoc failed to compile the .proto file (see above)")
        fds = descriptor_pb2.FileDescriptorSet.FromString(out.read_bytes())

    pool = descriptor_pool.DescriptorPool()
    for fdp in fds.file:  # topologically sorted by protoc
        pool.AddSerializedFile(fdp.SerializeToString())
    names = [f.name for f in fds.file]
    return pool, pool.FindFileByName(names[-1]), names


def message_class(pool, desc):
    """Build a concrete message class for ``desc`` across protobuf versions."""
    if hasattr(message_factory, "GetMessageClass"):  # protobuf >= 4.22
        return message_factory.GetMessageClass(desc)
    factory = message_factory.MessageFactory(pool)  # pragma: no cover - old protobuf
    if hasattr(factory, "GetPrototype"):  # pragma: no cover
        return factory.GetPrototype(desc)
    return factory.GetMessages([desc.file.name])[desc.full_name]  # pragma: no cover


def _walk_messages(descs) -> Iterator:
    """Yield every message descriptor, including nested types, depth-first."""
    for d in descs:
        yield d
        yield from _walk_messages(d.nested_types)


def all_messages(pool, file_names: Iterable[str]) -> list:
    """Every message (top-level and nested) defined in the given files."""
    out = []
    for name in file_names:
        out.extend(_walk_messages(pool.FindFileByName(name).message_types_by_name.values()))
    return out


def pick_message(pool, file_desc, explicit: str | None, all_files: Iterable[str] = ()):
    """Resolve the message type to use.

    ``explicit`` may be the fully-qualified name (``acme.v1.Order``), with or
    without a leading dot, or any unambiguous dotted suffix of it (``Order``,
    ``v1.Order``, ``Order.Note``). The requested file is searched first, then its
    imports. With no ``explicit`` name the file must have exactly one top-level
    message.
    """
    own = all_messages(pool, [file_desc.name])
    if explicit:
        name = explicit.strip().lstrip(".")
        try:
            return pool.FindMessageTypeByName(name)
        except KeyError:
            pass
        # Suffix match: prefer the requested file, fall back to imported files.
        for candidates in (own, all_messages(pool, all_files)):
            hits = [
                d for d in candidates if d.full_name == name or d.full_name.endswith("." + name)
            ]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise ConversionError(
                    f"Message '{explicit}' is ambiguous; use one of: "
                    f"{', '.join(d.full_name for d in hits)}"
                )
        names = ", ".join(d.full_name for d in own) or "(none)"
        raise ConversionError(f"Message '{explicit}' not found. Available: {names}")

    msgs = list(file_desc.message_types_by_name.values())
    if len(msgs) == 1:
        return msgs[0]
    raise ConversionError(
        f"The proto has {len(msgs)} top-level messages; choose one with --message. "
        f"Available: {', '.join(d.full_name for d in msgs) or '(none)'}"
    )


# --------------------------------------------------------------------------- parsing


def parse_text(cls, data: bytes):
    """Parse protobuf text format into a new ``cls`` instance."""
    msg = cls()
    try:
        text_format.Parse(data.decode("utf-8"), msg)
    except (UnicodeDecodeError, text_format.ParseError) as e:
        raise ConversionError(f"Not valid protobuf text for this message: {e}") from None
    return msg


def has_unknown_fields(msg) -> bool:
    """True if ``msg`` (top level) carries fields its schema doesn't define."""
    try:  # protobuf >= 4.21; the only option on the upb backend
        from google.protobuf.unknown_fields import UnknownFieldSet

        return len(UnknownFieldSet(msg)) > 0
    except ImportError:  # pragma: no cover - old protobuf
        pass
    try:  # pragma: no cover - old protobuf
        return len(msg.UnknownFields()) > 0
    except Exception:  # pragma: no cover
        return False


def parse_binary(cls, data: bytes, warn: TextIO | bool | None = True):
    """Parse binary wire format into a new ``cls`` instance.

    Bytes that parse but contain fields the schema doesn't know usually mean the
    wrong message type or an outdated .proto, so a warning is written: to the
    current ``sys.stderr`` when ``warn`` is ``True`` (the default), to ``warn``
    itself when it is a stream, and not at all when it is ``False``/``None``.
    """
    msg = cls()
    try:
        msg.ParseFromString(data)
    except DecodeError as e:
        raise ConversionError(f"Not valid protobuf binary for this message: {e}") from None
    if warn is True:
        warn = sys.stderr  # resolved per call so redirection is honoured
    if warn and has_unknown_fields(msg):
        print(
            "warning: input has fields not in the .proto (wrong --message, or "
            "outdated .proto?); they are dropped from the text output",
            file=warn,
        )
    return msg


def detect_format(path: Path | None, forced: str | None, cls, data: bytes):
    """Parse ``data`` and return ``(format, message)``.

    The format is ``forced`` if given, else inferred from ``path``'s extension,
    else text is tried first and binary second.
    """
    suffix = path.suffix.lower() if path is not None else ""
    if forced == "text" or (forced is None and suffix in TEXT_SUFFIXES):
        return "text", parse_text(cls, data)
    if forced == "binary" or (forced is None and suffix in BINARY_SUFFIXES):
        return "binary", parse_binary(cls, data)
    if forced is not None:
        raise ConversionError(f"Unknown format: {forced!r} (expected one of {FORMATS})")
    try:  # unknown extension: try text first, then binary
        return "text", parse_text(cls, data)
    except ConversionError:
        return "binary", parse_binary(cls, data)


def serialize(msg, fmt: str) -> bytes:
    """Serialize ``msg`` as ``"text"`` or (deterministic) ``"binary"``."""
    if fmt == "text":
        return text_format.MessageToString(msg, as_utf8=True).encode("utf-8")
    if fmt == "binary":
        return msg.SerializeToString(deterministic=True)
    raise ConversionError(f"Unknown format: {fmt!r} (expected one of {FORMATS})")


def convert(
    proto_file: str | Path,
    data: bytes,
    *,
    message: str | None = None,
    includes: Iterable[str | Path] = (),
    src: str | None = None,
    dst: str | None = None,
    path: Path | None = None,
) -> tuple[str, str, bytes]:
    """One-shot conversion. Returns ``(src_format, dst_format, payload)``.

    ``dst`` defaults to the opposite of the detected input format.
    """
    pool, file_desc, names = load_proto(proto_file, includes)
    cls = message_class(pool, pick_message(pool, file_desc, message, names))
    src_fmt, msg = detect_format(path, src, cls, data)
    dst_fmt = dst or ("binary" if src_fmt == "text" else "text")
    return src_fmt, dst_fmt, serialize(msg, dst_fmt)
