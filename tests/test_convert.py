"""Tests for parsing, format detection, serialization and the convert() API."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pbconvert import (
    ConversionError,
    convert,
    detect_format,
    parse_binary,
    parse_text,
    serialize,
)
from pbconvert.core import BINARY_SUFFIXES, TEXT_SUFFIXES, has_unknown_fields


class TestParseText:
    def test_parses(self, order_cls, order_text):
        msg = parse_text(order_cls, order_text.encode())
        assert msg.id == 7
        assert [i.sku for i in msg.items] == ["abc", "déjà-vu"]
        assert msg.items[0].price.units == 3
        assert msg.note.text == "hello"
        assert msg.labels["env"] == "prod"
        assert msg.blob == b"\x01\x02"
        assert msg.created_at.seconds == 1700000000

    def test_empty_input_is_default_message(self, order_cls):
        assert parse_text(order_cls, b"") == order_cls()

    def test_comments_allowed(self, order_cls):
        msg = parse_text(order_cls, b"# a comment\nid: 1  # trailing\n")
        assert msg.id == 1

    def test_unknown_field(self, order_cls):
        with pytest.raises(ConversionError, match="no field named"):
            parse_text(order_cls, b"bogus: 1")

    def test_wrong_type(self, order_cls):
        with pytest.raises(ConversionError, match="Not valid protobuf text"):
            parse_text(order_cls, b'id: "seven"')

    def test_not_utf8(self, order_cls):
        with pytest.raises(ConversionError, match="Not valid protobuf text"):
            parse_text(order_cls, b"\xff\xfe\x00")


class TestParseBinary:
    def test_parses(self, order_cls, order_binary):
        msg = parse_binary(order_cls, order_binary, warn=None)
        assert msg.id == 7
        assert msg.items[1].sku == "déjà-vu"

    def test_empty_input_is_default_message(self, order_cls):
        assert parse_binary(order_cls, b"", warn=None) == order_cls()

    def test_truncated(self, order_cls, order_binary):
        with pytest.raises(ConversionError, match="Not valid protobuf binary"):
            parse_binary(order_cls, order_binary[:-1], warn=None)

    def test_garbage(self, order_cls):
        with pytest.raises(ConversionError, match="Not valid protobuf binary"):
            parse_binary(order_cls, b"\xff\xff\xff\xff", warn=None)

    def test_warns_on_unknown_fields(self, order_cls):
        buf = io.StringIO()
        # Field 99, varint 1: not in the schema.
        msg = parse_binary(order_cls, b"\x98\x06\x01", warn=buf)
        assert msg.ListFields() == []  # nothing known was set
        assert "fields not in the .proto" in buf.getvalue()

    def test_no_warning_for_known_fields(self, order_cls, order_binary):
        buf = io.StringIO()
        parse_binary(order_cls, order_binary, warn=buf)
        assert buf.getvalue() == ""

    @pytest.mark.parametrize("warn", [None, False])
    def test_warning_can_be_silenced(self, order_cls, capsys, warn):
        parse_binary(order_cls, b"\x98\x06\x01", warn=warn)
        assert capsys.readouterr().err == ""

    def test_warns_to_current_stderr_by_default(self, order_cls, capsys):
        parse_binary(order_cls, b"\x98\x06\x01")
        assert "warning:" in capsys.readouterr().err


class TestHasUnknownFields:
    def test_true(self, order_cls):
        msg = order_cls()
        msg.ParseFromString(b"\x98\x06\x01")
        assert has_unknown_fields(msg)

    def test_false(self, order_cls):
        assert not has_unknown_fields(order_cls(id=1))


class TestDetectFormat:
    @pytest.mark.parametrize("suffix", sorted(TEXT_SUFFIXES))
    def test_text_suffixes(self, order_cls, order_text, suffix):
        fmt, msg = detect_format(Path(f"x{suffix}"), None, order_cls, order_text.encode())
        assert fmt == "text" and msg.id == 7

    @pytest.mark.parametrize("suffix", sorted(BINARY_SUFFIXES))
    def test_binary_suffixes(self, order_cls, order_binary, suffix):
        fmt, msg = detect_format(Path(f"x{suffix}"), None, order_cls, order_binary)
        assert fmt == "binary" and msg.id == 7

    def test_suffix_is_case_insensitive(self, order_cls, order_text):
        fmt, _ = detect_format(Path("X.TXTPB"), None, order_cls, order_text.encode())
        assert fmt == "text"

    def test_forced_overrides_suffix(self, order_cls, order_binary):
        fmt, msg = detect_format(Path("x.txt"), "binary", order_cls, order_binary)
        assert fmt == "binary" and msg.id == 7

    def test_forced_text_errors_on_binary(self, order_cls, order_binary):
        with pytest.raises(ConversionError, match="Not valid protobuf text"):
            detect_format(Path("x.pb"), "text", order_cls, order_binary)

    def test_text_suffix_does_not_fall_back(self, order_cls, order_binary):
        with pytest.raises(ConversionError, match="Not valid protobuf text"):
            detect_format(Path("x.txtpb"), None, order_cls, order_binary)

    def test_unknown_suffix_tries_text_first(self, order_cls, order_text):
        fmt, msg = detect_format(Path("x.dat"), None, order_cls, order_text.encode())
        assert fmt == "text" and msg.id == 7

    def test_unknown_suffix_falls_back_to_binary(self, order_cls, order_binary):
        fmt, msg = detect_format(Path("x.dat"), None, order_cls, order_binary)
        assert fmt == "binary" and msg.id == 7

    def test_no_path_sniffs(self, order_cls, order_binary):
        fmt, _ = detect_format(None, None, order_cls, order_binary)
        assert fmt == "binary"

    def test_unknown_suffix_neither_format(self, order_cls):
        with pytest.raises(ConversionError, match="Not valid protobuf binary"):
            detect_format(Path("x.dat"), None, order_cls, b"\xff\xff\xff\xff")

    def test_invalid_forced_format(self, order_cls):
        with pytest.raises(ConversionError, match="Unknown format"):
            detect_format(Path("x.pb"), "json", order_cls, b"")


class TestSerialize:
    def test_text(self, order_cls):
        assert serialize(order_cls(id=3), "text") == b"id: 3\n"

    def test_text_keeps_utf8(self, order_cls, order_text):
        out = serialize(parse_text(order_cls, order_text.encode()), "text")
        assert "déjà-vu".encode() in out

    def test_binary(self, order_cls):
        assert serialize(order_cls(id=3), "binary") == b"\x08\x03"

    def test_binary_is_deterministic(self, order_cls):
        msg = order_cls()
        for k in "zyxwvutsrq":
            msg.labels[k] = k
        assert serialize(msg, "binary") == serialize(msg, "binary")
        reordered = order_cls()
        for k in sorted("zyxwvutsrq"):
            reordered.labels[k] = k
        assert serialize(msg, "binary") == serialize(reordered, "binary")

    def test_unknown_format(self, order_cls):
        with pytest.raises(ConversionError, match="Unknown format"):
            serialize(order_cls(), "yaml")


class TestConvert:
    def test_text_to_binary(self, orders_proto, order_text, order_binary):
        src, dst, out = convert(
            orders_proto, order_text.encode(), message="Order", path=Path("o.txtpb")
        )
        assert (src, dst) == ("text", "binary")
        assert out == order_binary

    def test_binary_to_text(self, orders_proto, order_binary, order_cls):
        src, dst, out = convert(orders_proto, order_binary, message="Order", path=Path("o.pb"))
        assert (src, dst) == ("binary", "text")
        assert parse_text(order_cls, out) == parse_binary(order_cls, order_binary, warn=None)

    def test_round_trip_is_lossless(self, orders_proto, order_text):
        _, _, binary = convert(orders_proto, order_text.encode(), message="Order", src="text")
        _, _, text = convert(orders_proto, binary, message="Order", src="binary")
        _, _, binary2 = convert(orders_proto, text, message="Order", src="text")
        assert binary == binary2

    def test_same_format_normalizes_text(self, orders_proto):
        _, dst, out = convert(
            orders_proto, b"id:   9   note{text:'x'}", message="Order", src="text", dst="text"
        )
        assert dst == "text"
        assert out == b'id: 9\nnote {\n  text: "x"\n}\n'

    def test_default_message(self, single_proto):
        src, dst, out = convert(single_proto, b'greeting: "hi" count: 2', src="text")
        assert out == b"\n\x02hi\x10\x02"

    def test_includes(self, protos, protos_extra):
        _, _, out = convert(
            protos / "uses_vendor.proto",
            b'ext { value: "v" }',
            includes=[protos_extra],
            src="text",
        )
        assert out == b"\n\x03\n\x01v"

    def test_errors_propagate(self, orders_proto):
        with pytest.raises(ConversionError, match="choose one"):
            convert(orders_proto, b"")
