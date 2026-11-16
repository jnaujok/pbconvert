"""Tests for .proto compilation and message-type resolution."""

from __future__ import annotations

import pytest

from pbconvert import ConversionError, all_messages, load_proto, message_class, pick_message


class TestLoadProto:
    def test_returns_requested_file_last(self, orders_schema):
        pool, fd, names = orders_schema
        assert fd.name == "orders.proto"
        assert names[-1] == "orders.proto"

    def test_includes_imports(self, orders_schema):
        _, _, names = orders_schema
        assert "sub/common.proto" in names
        assert "google/protobuf/timestamp.proto" in names

    def test_imports_come_before_dependents(self, orders_schema):
        _, _, names = orders_schema
        assert names.index("sub/common.proto") < names.index("orders.proto")

    def test_accepts_str_path(self, orders_proto):
        _, fd, _ = load_proto(str(orders_proto))
        assert fd.package == "acme.v1"

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConversionError, match="Proto file not found"):
            load_proto(tmp_path / "nope.proto")

    def test_directory_is_not_a_proto(self, tmp_path):
        with pytest.raises(ConversionError, match="Proto file not found"):
            load_proto(tmp_path)

    def test_syntax_error(self, protos):
        with pytest.raises(ConversionError, match="protoc failed"):
            load_proto(protos / "broken.proto")

    def test_missing_import_without_include(self, protos):
        with pytest.raises(ConversionError, match="protoc failed"):
            load_proto(protos / "uses_vendor.proto")

    def test_extra_include_path(self, protos, protos_extra):
        _, fd, names = load_proto(protos / "uses_vendor.proto", [protos_extra])
        assert fd.name == "uses_vendor.proto"
        assert "vendor/ext.proto" in names

    def test_pools_are_isolated(self, orders_proto):
        pool1, _, _ = load_proto(orders_proto)
        pool2, _, _ = load_proto(orders_proto)
        assert pool1 is not pool2
        # Loading the same file twice must not clash (each call has its own pool).
        assert pool1.FindMessageTypeByName("acme.v1.Order").full_name == "acme.v1.Order"
        assert pool2.FindMessageTypeByName("acme.v1.Order").full_name == "acme.v1.Order"


class TestAllMessages:
    def test_includes_nested(self, orders_schema):
        pool, fd, _ = orders_schema
        names = {d.full_name for d in all_messages(pool, [fd.name])}
        assert {"acme.v1.Item", "acme.v1.Order", "acme.v1.Order.Note"} <= names

    def test_includes_map_entry_types(self, orders_schema):
        # protoc synthesizes a nested *Entry message for map fields.
        pool, fd, _ = orders_schema
        names = {d.full_name for d in all_messages(pool, [fd.name])}
        assert "acme.v1.Order.LabelsEntry" in names

    def test_across_files(self, orders_schema):
        pool, _, names = orders_schema
        found = {d.full_name for d in all_messages(pool, names)}
        assert "acme.common.Money" in found
        assert "google.protobuf.Timestamp" in found

    def test_empty(self):
        assert all_messages(None, []) == []


class TestPickMessage:
    @pytest.mark.parametrize(
        "name",
        [
            "acme.v1.Order",
            ".acme.v1.Order",
            "  acme.v1.Order  ",
            "Order",
            "v1.Order",
        ],
    )
    def test_resolves_order(self, orders_schema, name):
        pool, fd, names = orders_schema
        assert pick_message(pool, fd, name, names).full_name == "acme.v1.Order"

    @pytest.mark.parametrize("name", ["acme.v1.Order.Note", "Order.Note", "Note"])
    def test_resolves_nested(self, orders_schema, name):
        pool, fd, names = orders_schema
        assert pick_message(pool, fd, name, names).full_name == "acme.v1.Order.Note"

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Money", "acme.common.Money"),
            ("common.Money", "acme.common.Money"),
            ("Timestamp", "google.protobuf.Timestamp"),
            ("google.protobuf.Timestamp", "google.protobuf.Timestamp"),
        ],
    )
    def test_resolves_from_imports(self, orders_schema, name, expected):
        pool, fd, names = orders_schema
        assert pick_message(pool, fd, name, names).full_name == expected

    def test_suffix_must_match_whole_component(self, orders_schema):
        # "rder" is a string suffix of "Order" but not a name component.
        pool, fd, names = orders_schema
        with pytest.raises(ConversionError, match="not found"):
            pick_message(pool, fd, "rder", names)

    def test_case_sensitive(self, orders_schema):
        pool, fd, names = orders_schema
        with pytest.raises(ConversionError, match="not found"):
            pick_message(pool, fd, "order", names)

    def test_not_found_lists_fully_qualified_names(self, orders_schema):
        pool, fd, names = orders_schema
        with pytest.raises(ConversionError) as exc:
            pick_message(pool, fd, "Nope", names)
        msg = str(exc.value)
        assert "Message 'Nope' not found" in msg
        assert "acme.v1.Item" in msg
        assert "acme.v1.Order" in msg
        assert "acme.v1.Order.Note" in msg

    def test_ambiguous(self, protos):
        pool, fd, names = load_proto(protos / "ambiguous.proto")
        with pytest.raises(ConversionError, match="ambiguous") as exc:
            pick_message(pool, fd, "A", names)
        assert "amb.A" in str(exc.value) and "amb.B.A" in str(exc.value)

    def test_ambiguity_resolved_by_qualifying(self, protos):
        pool, fd, names = load_proto(protos / "ambiguous.proto")
        assert pick_message(pool, fd, "B.A", names).full_name == "amb.B.A"
        assert pick_message(pool, fd, "amb.A", names).full_name == "amb.A"

    def test_requested_file_wins_over_imports(self, tmp_path):
        (tmp_path / "dep.proto").write_text(
            'syntax = "proto3"; package dep; message Widget { int32 x = 1; }'
        )
        (tmp_path / "main.proto").write_text(
            'syntax = "proto3"; package main; import "dep.proto";'
            " message Widget { dep.Widget d = 1; }"
        )
        pool, fd, names = load_proto(tmp_path / "main.proto")
        assert pick_message(pool, fd, "Widget", names).full_name == "main.Widget"

    def test_without_all_files_only_searches_requested_file(self, orders_schema):
        pool, fd, _ = orders_schema
        with pytest.raises(ConversionError, match="not found"):
            pick_message(pool, fd, "Money")

    def test_no_package(self, protos):
        pool, fd, names = load_proto(protos / "nopackage.proto")
        assert pick_message(pool, fd, "Thing", names).full_name == "Thing"
        assert pick_message(pool, fd, ".Other", names).full_name == "Other"

    def test_default_single_message(self, single_proto):
        pool, fd, names = load_proto(single_proto)
        assert pick_message(pool, fd, None, names).full_name == "demo.Ping"

    def test_default_empty_string_treated_as_unset(self, single_proto):
        pool, fd, names = load_proto(single_proto)
        assert pick_message(pool, fd, "", names).full_name == "demo.Ping"

    def test_default_ignores_nested_types(self, protos):
        # ambiguous.proto has 2 top-level messages (A, B) plus nested B.A.
        pool, fd, names = load_proto(protos / "ambiguous.proto")
        with pytest.raises(ConversionError, match="2 top-level messages"):
            pick_message(pool, fd, None, names)

    def test_default_requires_choice_when_several(self, orders_schema):
        pool, fd, names = orders_schema
        with pytest.raises(ConversionError, match="choose one with --message") as exc:
            pick_message(pool, fd, None, names)
        assert "acme.v1.Item, acme.v1.Order" in str(exc.value)

    def test_default_with_no_messages(self, protos):
        pool, fd, names = load_proto(protos / "empty.proto")
        with pytest.raises(ConversionError, match=r"0 top-level messages.*\(none\)"):
            pick_message(pool, fd, None, names)

    def test_not_found_with_no_messages(self, protos):
        pool, fd, names = load_proto(protos / "empty.proto")
        with pytest.raises(ConversionError, match=r"Available: \(none\)"):
            pick_message(pool, fd, "X", names)


class TestMessageClass:
    def test_builds_instantiable_class(self, orders_schema):
        pool, fd, names = orders_schema
        cls = message_class(pool, pick_message(pool, fd, "Order", names))
        msg = cls(id=5)
        assert msg.id == 5
        assert msg.DESCRIPTOR.full_name == "acme.v1.Order"

    def test_nested_class(self, orders_schema):
        pool, fd, names = orders_schema
        cls = message_class(pool, pick_message(pool, fd, "Order.Note", names))
        assert cls(text="x").text == "x"
