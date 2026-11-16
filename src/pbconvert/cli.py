"""Command-line interface for pbconvert."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .core import FORMATS, ConversionError, convert

EPILOG = """\
examples:
  pbconvert messages.proto out.pb                       # binary -> out.txtpb
  pbconvert messages.proto out.txtpb                    # text   -> out.pb
  pbconvert messages.proto in.bin -m acme.v1.Order -o in.txt
  pbconvert messages.proto in.bin -m Order --from binary --to text -o -
  cat in.bin | pbconvert messages.proto - --from binary -o -

input format detection: --from, else file extension (.txtpb .textproto .pbtxt
.prototxt .txt = text; .pb .bin .binpb = binary), else try text then binary.
"""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pbconvert",
        description="Convert a protobuf message between binary wire format and text format.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("proto", help=".proto file defining the message")
    ap.add_argument("input", help="input file (binary or text protobuf); '-' = stdin")
    ap.add_argument(
        "-o",
        "--output",
        help="output file ('-' = stdout); default: input with the extension swapped",
    )
    ap.add_argument(
        "-m",
        "--message",
        help="message type, e.g. acme.v1.Order or just Order (required if the proto has several)",
    )
    ap.add_argument(
        "-I", "--include", action="append", default=[], help="extra proto import path (repeatable)"
    )
    ap.add_argument("--from", dest="src", choices=FORMATS, help="input format (default: detect)")
    ap.add_argument(
        "--to",
        dest="dst",
        choices=FORMATS,
        help="output format (default: the opposite of the input)",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    from_stdin = a.input == "-"
    in_path = None if from_stdin else Path(a.input)

    if from_stdin and a.output in (None, ""):
        print("error: reading from stdin requires -o/--output", file=sys.stderr)
        return 1

    try:
        if from_stdin:
            data = sys.stdin.buffer.read()
        else:
            if not in_path.is_file():
                raise ConversionError(f"Input file not found: {a.input}")
            data = in_path.read_bytes()
        src, dst, payload = convert(
            a.proto, data, message=a.message, includes=a.include, src=a.src, dst=a.dst, path=in_path
        )
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if a.output == "-":
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return 0

    out = Path(a.output) if a.output else in_path.with_suffix(".txtpb" if dst == "text" else ".pb")
    if in_path is not None and out.resolve() == in_path.resolve():
        print("error: output would overwrite the input; use -o", file=sys.stderr)
        return 1
    out.write_bytes(payload)
    print(f"{src} -> {dst}: wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
