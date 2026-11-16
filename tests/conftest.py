"""Shared fixtures for the pbconvert test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from pbconvert import load_proto, message_class, pick_message

PROTOS = Path(__file__).parent / "protos"
PROTOS_EXTRA = Path(__file__).parent / "protos_extra"

ORDER_TEXT = """\
id: 7
items {
  sku: "abc"
  price {
    currency: "USD"
    units: 3
  }
}
items {
  sku: "déjà-vu"
}
created_at {
  seconds: 1700000000
}
note {
  text: "hello"
}
labels {
  key: "env"
  value: "prod"
}
blob: "\\001\\002"
"""


@pytest.fixture(scope="session")
def protos() -> Path:
    return PROTOS


@pytest.fixture(scope="session")
def protos_extra() -> Path:
    return PROTOS_EXTRA


@pytest.fixture(scope="session")
def orders_proto() -> Path:
    return PROTOS / "orders.proto"


@pytest.fixture(scope="session")
def single_proto() -> Path:
    return PROTOS / "single.proto"


@pytest.fixture(scope="session")
def orders_schema(orders_proto):
    """(pool, file_descriptor, file_names) for orders.proto."""
    return load_proto(orders_proto)


@pytest.fixture(scope="session")
def order_cls(orders_schema):
    pool, fd, names = orders_schema
    return message_class(pool, pick_message(pool, fd, "acme.v1.Order", names))


@pytest.fixture(scope="session")
def order_text() -> str:
    return ORDER_TEXT


@pytest.fixture(scope="session")
def order_binary(order_cls) -> bytes:
    from google.protobuf import text_format

    msg = order_cls()
    text_format.Parse(ORDER_TEXT, msg)
    return msg.SerializeToString(deterministic=True)
