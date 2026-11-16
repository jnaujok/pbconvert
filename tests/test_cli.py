"""End-to-end tests for the command-line interface."""

from __future__ import annotations

import io
import subprocess
import sys

import pytest

import pbconvert
from pbconvert.cli import build_parser, main


@pytest.fixture
def txt_file(tmp_path, order_text):
    p = tmp_path / "order.txtpb"
    p.write_text(order_text, encoding="utf-8")
    return p


@pytest.fixture
def bin_file(tmp_path, order_binary):
    p = tmp_path / "order.pb"
    p.write_bytes(order_binary)
    return p


class _FakeStdio:
    """Minimal stand-in for sys.stdin/sys.stdout with a binary .buffer."""

    def __init__(self, data: bytes = b""):
        self.buffer = io.BytesIO(data)


class TestDefaultOutputPath:
    def test_text_to_binary(self, orders_proto, txt_file, order_binary, capsys):
        assert main([str(orders_proto), str(txt_file), "-m", "Order"]) == 0
        out = txt_file.with_suffix(".pb")
        assert out.read_bytes() == order_binary
        assert f"text -> binary: wrote {out}" in capsys.readouterr().err

    def test_binary_to_text(self, orders_proto, bin_file, capsys):
        assert main([str(orders_proto), str(bin_file), "-m", "acme.v1.Order"]) == 0
        out = bin_file.with_suffix(".txtpb")
        assert out.read_text(encoding="utf-8").startswith("id: 7\n")
        assert "binary -> text" in capsys.readouterr().err

    def test_refuses_to_overwrite_input(self, orders_proto, tmp_path, order_text, capsys):
        # .txtpb input with --to text would default to writing the same file.
        p = tmp_path / "o.txtpb"
        p.write_text(order_text, encoding="utf-8")
        assert main([str(orders_proto), str(p), "-m", "Order", "--to", "text"]) == 1
        assert "would overwrite the input" in capsys.readouterr().err
        assert p.read_text(encoding="utf-8") == order_text

    def test_refuses_explicit_overwrite(self, orders_proto, bin_file, order_binary, capsys):
        args = [str(orders_proto), str(bin_file), "-m", "Order", "--to", "binary"]
        assert main([*args, "-o", str(bin_file)]) == 1
        assert bin_file.read_bytes() == order_binary


class TestExplicitOutput:
    def test_output_file(self, orders_proto, bin_file, tmp_path):
        out = tmp_path / "nested.txt"
        assert main([str(orders_proto), str(bin_file), "-m", "Order", "-o", str(out)]) == 0
        assert 'sku: "abc"' in out.read_text(encoding="utf-8")

    def test_stdout(self, orders_proto, bin_file, monkeypatch, capsys):
        fake = _FakeStdio()
        monkeypatch.setattr(sys, "stdout", fake)
        assert main([str(orders_proto), str(bin_file), "-m", "Order", "-o", "-"]) == 0
        assert fake.buffer.getvalue().startswith(b"id: 7\n")
        assert capsys.readouterr().err == ""  # no "wrote ..." banner on stdout mode


class TestStdin:
    def test_stdin_to_stdout(self, orders_proto, order_binary, monkeypatch):
        out = _FakeStdio()
        monkeypatch.setattr(sys, "stdin", _FakeStdio(order_binary))
        monkeypatch.setattr(sys, "stdout", out)
        argv = [str(orders_proto), "-", "-m", "Order", "--from", "binary", "-o", "-"]
        assert main(argv) == 0
        assert out.buffer.getvalue().startswith(b"id: 7\n")

    def test_stdin_sniffs_format(self, orders_proto, order_text, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "stdin", _FakeStdio(order_text.encode()))
        out = tmp_path / "o.pb"
        assert main([str(orders_proto), "-", "-m", "Order", "-o", str(out)]) == 0
        assert out.read_bytes().startswith(b"\x08\x07")

    def test_stdin_requires_output(self, orders_proto, capsys):
        assert main([str(orders_proto), "-", "-m", "Order"]) == 1
        assert "requires -o" in capsys.readouterr().err


class TestFormatFlags:
    def test_from_overrides_extension(self, orders_proto, tmp_path, order_binary):
        p = tmp_path / "actually_binary.txt"
        p.write_bytes(order_binary)
        out = tmp_path / "o.txtpb"
        argv = [str(orders_proto), str(p), "-m", "Order", "--from", "binary", "-o", str(out)]
        assert main(argv) == 0
        assert out.read_text(encoding="utf-8").startswith("id: 7")

    def test_to_same_format(self, orders_proto, bin_file, tmp_path, order_binary):
        out = tmp_path / "copy.bin"
        argv = [str(orders_proto), str(bin_file), "-m", "Order", "--to", "binary", "-o", str(out)]
        assert main(argv) == 0
        assert out.read_bytes() == order_binary

    def test_default_extension_follows_to(self, orders_proto, bin_file):
        assert (
            main(
                [
                    str(orders_proto),
                    str(bin_file),
                    "-m",
                    "Order",
                    "--to",
                    "binary",
                    "-o",
                    str(bin_file.with_name("x.pb")),
                ]
            )
            == 0
        )
        assert bin_file.with_name("x.pb").exists()

    def test_invalid_choice(self, orders_proto, bin_file, capsys):
        with pytest.raises(SystemExit) as exc:
            main([str(orders_proto), str(bin_file), "--from", "json"])
        assert exc.value.code == 2
        assert "invalid choice" in capsys.readouterr().err


class TestIncludes:
    def test_include_flag(self, protos, protos_extra, tmp_path):
        inp = tmp_path / "w.txtpb"
        inp.write_text('ext { value: "v" }', encoding="utf-8")
        out = tmp_path / "w.pb"
        argv = [
            str(protos / "uses_vendor.proto"),
            str(inp),
            "-I",
            str(protos_extra),
            "-o",
            str(out),
        ]
        assert main(argv) == 0
        assert out.read_bytes() == b"\n\x03\n\x01v"

    def test_include_long_form_repeatable(self):
        a = build_parser().parse_args(["p.proto", "i", "--include", "a", "-I", "b"])
        assert a.include == ["a", "b"]


class TestErrors:
    def test_missing_input(self, orders_proto, tmp_path, capsys):
        assert main([str(orders_proto), str(tmp_path / "nope.pb"), "-m", "Order"]) == 1
        assert "Input file not found" in capsys.readouterr().err

    def test_missing_proto(self, tmp_path, bin_file, capsys):
        assert main([str(tmp_path / "nope.proto"), str(bin_file)]) == 1
        assert "Proto file not found" in capsys.readouterr().err

    def test_unknown_message(self, orders_proto, bin_file, capsys):
        assert main([str(orders_proto), str(bin_file), "-m", "Nope"]) == 1
        err = capsys.readouterr().err
        assert "error: Message 'Nope' not found" in err
        assert "acme.v1.Order.Note" in err

    def test_message_required(self, orders_proto, bin_file, capsys):
        assert main([str(orders_proto), str(bin_file)]) == 1
        assert "choose one with --message" in capsys.readouterr().err

    def test_bad_binary(self, orders_proto, tmp_path, capsys):
        p = tmp_path / "bad.pb"
        p.write_bytes(b"\xff\xff\xff\xff")
        assert main([str(orders_proto), str(p), "-m", "Order"]) == 1
        assert "Not valid protobuf binary" in capsys.readouterr().err

    def test_wrong_message_warns(self, orders_proto, bin_file, tmp_path, capsys):
        # Order bytes decoded as Order.Note: only field 1 matches; 2-6 are unknown.
        out = tmp_path / "o.txtpb"
        rc = main([str(orders_proto), str(bin_file), "-m", "Note", "-o", str(out)])
        err = capsys.readouterr().err
        assert rc == 0
        assert "warning: input has fields not in the .proto" in err

    def test_no_args(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2


class TestMeta:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert pbconvert.__version__ in capsys.readouterr().out

    def test_help_has_examples(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "examples:" in out and "--message" in out

    def test_python_dash_m(self, orders_proto, bin_file):
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pbconvert",
                str(orders_proto),
                str(bin_file),
                "-m",
                "Order",
                "-o",
                "-",
            ],
            capture_output=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith(b"id: 7\n")

    def test_python_dash_m_exit_code(self, orders_proto, tmp_path):
        r = subprocess.run(
            [sys.executable, "-m", "pbconvert", str(orders_proto), str(tmp_path / "x.pb")],
            capture_output=True,
            check=False,
        )
        assert r.returncode == 1
        assert b"Input file not found" in r.stderr
