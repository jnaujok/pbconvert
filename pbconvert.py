#!/usr/bin/env python3
"""
pbconvert.py - Convert a protobuf message between binary wire format and
protobuf text format, using a .proto file for the schema.

Binary protobuf has no field names, so the .proto (and the message type) is
required in both directions. The direction is chosen automatically: text in
-> binary out, binary in -> text out.

Install:  pip install protobuf grpcio-tools

Usage:
  python pbconvert.py messages.proto out.pb                  # binary -> out.txtpb
  python pbconvert.py messages.proto out.txtpb               # text   -> out.pb
  python pbconvert.py messages.proto in.bin -m acme.v1.Order -o in.txt
  python pbconvert.py messages.proto in.bin --from binary --to text -o -
Input format detection: --from, else file extension (.txtpb .textproto .pbtxt
.txt = text, anything else = binary), else try text then binary.
"""
from __future__ import annotations

import argparse
import importlib.resources as ir
import sys
import tempfile
from pathlib import Path

try:
    from google.protobuf import (
        descriptor_pb2,
        descriptor_pool,
        message_factory,
        text_format,
    )
    from google.protobuf.message import DecodeError
    from grpc_tools import protoc
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Missing dependency: {exc.name}\n"
             "Install with: pip install protobuf grpcio-tools")

TEXT_SUFFIXES = {".txtpb", ".textproto", ".pbtxt", ".txt", ".prototxt"}


class ConversionError(Exception):
    pass


def load_proto(proto_file: str, includes: list[str]):
    """Compile .proto to a descriptor set (in-process) and load it into a private pool."""
    proto_path = Path(proto_file).resolve()
    if not proto_path.exists():
        raise ConversionError(f"Proto file not found: {proto_file}")
    wkt_dir = str(ir.files("grpc_tools") / "_proto")  # google/protobuf/*.proto

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "desc.bin"
        args = ["protoc", f"-I{proto_path.parent}",
                *[f"-I{i}" for i in includes], f"-I{wkt_dir}",
                f"--descriptor_set_out={out}", "--include_imports",
                str(proto_path)]
        if protoc.main(args) != 0:
            raise ConversionError("protoc failed to compile the .proto file (see above)")
        fds = descriptor_pb2.FileDescriptorSet.FromString(out.read_bytes())

    pool = descriptor_pool.DescriptorPool()
    for fdp in fds.file:  # topologically sorted by protoc
        pool.AddSerializedFile(fdp.SerializeToString())
    names = [f.name for f in fds.file]
    return pool, pool.FindFileByName(names[-1]), names  # last = requested file


def message_class(pool, desc):
    """Build a message class across protobuf versions (3.x - 6.x)."""
    if hasattr(message_factory, "GetMessageClass"):  # protobuf >= 4.22
        return message_factory.GetMessageClass(desc)
    factory = message_factory.MessageFactory(pool)
    if hasattr(factory, "GetPrototype"):  # older protobuf
        return factory.GetPrototype(desc)
    return factory.GetMessages([desc.file.name])[desc.full_name]


def _walk_messages(descs):
    """Yield every message descriptor, including nested types, depth-first."""
    for d in descs:
        yield d
        yield from _walk_messages(d.nested_types)


def all_messages(pool, file_names):
    """Every message (top-level and nested) defined in the given files."""
    out = []
    for name in file_names:
        out.extend(_walk_messages(pool.FindFileByName(name).message_types_by_name.values()))
    return out


def pick_message(pool, file_desc, explicit: str | None, all_files: list[str]):
    """Resolve --message. Accepts the fully-qualified name (acme.v1.Order), with or
    without a leading dot, or any unambiguous dotted suffix of it (Order,
    v1.Order, Order.Item). Searches the requested file first, then its imports."""
    own = all_messages(pool, [file_desc.name])
    if explicit:
        name = explicit.strip().lstrip(".")
        try:
            return pool.FindMessageTypeByName(name)
        except KeyError:
            pass
        # Suffix match: prefer the requested file, fall back to imported files.
        for candidates in (own, all_messages(pool, all_files)):
            hits = [d for d in candidates
                    if d.full_name == name or d.full_name.endswith("." + name)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise ConversionError(
                    f"Message '{explicit}' is ambiguous; use one of: "
                    f"{', '.join(d.full_name for d in hits)}")
        names = ", ".join(d.full_name for d in own) or "(none)"
        raise ConversionError(f"Message '{explicit}' not found. Available: {names}")

    msgs = list(file_desc.message_types_by_name.values())
    if len(msgs) == 1:
        return msgs[0]
    raise ConversionError(
        f"The proto has {len(msgs)} top-level messages; choose one with --message. "
        f"Available: {', '.join(d.full_name for d in msgs) or '(none)'}")


def parse_text(cls, data: bytes):
    msg = cls()
    try:
        text_format.Parse(data.decode("utf-8"), msg)
    except (UnicodeDecodeError, text_format.ParseError) as e:
        raise ConversionError(f"Not valid protobuf text for this message: {e}") from None
    return msg


def parse_binary(cls, data: bytes):
    msg = cls()
    try:
        msg.ParseFromString(data)
    except DecodeError as e:
        raise ConversionError(f"Not valid protobuf binary for this message: {e}") from None
    # Bytes that parse but contain fields the schema doesn't know usually mean
    # the wrong message type or an outdated .proto.
    try:
        if msg.UnknownFields():
            print("warning: input has fields not in the .proto (wrong --message, or "
                  "outdated .proto?); they are dropped from the text output",
                  file=sys.stderr)
    except Exception:
        pass
    return msg


def detect_format(path: Path, forced: str | None, cls, data: bytes):
    """-> (format, message)"""
    if forced == "text":
        return "text", parse_text(cls, data)
    if forced == "binary":
        return "binary", parse_binary(cls, data)
    if path.suffix.lower() in TEXT_SUFFIXES:
        return "text", parse_text(cls, data)
    if path.suffix.lower() in {".pb", ".bin", ".binpb"}:
        return "binary", parse_binary(cls, data)
    try:  # unknown extension: try text first, then binary
        return "text", parse_text(cls, data)
    except ConversionError:
        return "binary", parse_binary(cls, data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Convert a protobuf message between binary and text format.")
    ap.add_argument("proto", help=".proto file defining the message")
    ap.add_argument("input", help="input file (binary or text protobuf)")
    ap.add_argument("-o", "--output", help="output file ('-' = stdout); default: swap the extension")
    ap.add_argument("-m", "--message",
                    help="message type, e.g. acme.v1.Order or just Order "
                         "(required if the proto has several)")
    ap.add_argument("-I", "--include", action="append", default=[], help="extra proto import path")
    ap.add_argument("--from", dest="src", choices=["binary", "text"],
                    help="input format (default: detect)")
    ap.add_argument("--to", dest="dst", choices=["binary", "text"],
                    help="output format (default: the opposite of the input)")
    a = ap.parse_args(argv)

    try:
        pool, file_desc, all_files = load_proto(a.proto, a.include)
        cls = message_class(pool, pick_message(pool, file_desc, a.message, all_files))
        in_path = Path(a.input)
        if not in_path.exists():
            raise ConversionError(f"Input file not found: {a.input}")
        src, msg = detect_format(in_path, a.src, cls, in_path.read_bytes())
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    dst = a.dst or ("binary" if src == "text" else "text")
    if dst == "text":
        payload = text_format.MessageToString(msg, as_utf8=True).encode("utf-8")
    else:
        payload = msg.SerializeToString(deterministic=True)

    if a.output == "-":
        sys.stdout.buffer.write(payload)
        return 0
    out = Path(a.output) if a.output else in_path.with_suffix(".txtpb" if dst == "text" else ".pb")
    if out.resolve() == in_path.resolve():
        print("error: output would overwrite the input; use -o", file=sys.stderr)
        return 1
    out.write_bytes(payload)
    print(f"{src} -> {dst}: wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
