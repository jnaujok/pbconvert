# pbconvert

[![CI](https://github.com/jnaujok/pbconvert/actions/workflows/ci.yml/badge.svg)](https://github.com/jnaujok/pbconvert/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Convert a [Protocol Buffers](https://protobuf.dev/) message between **binary wire
format** (`.pb`, `.bin`, `.binpb`) and **protobuf text format** (`.txtpb`,
`.textproto`, `.pbtxt`, ...), using the `.proto` file as the schema.

```console
$ pbconvert orders.proto order.pb -m Order
binary -> text: wrote order.txtpb

$ cat order.txtpb
id: 7
items {
  sku: "abc"
  price {
    currency: "USD"
    units: 3
  }
}

$ pbconvert orders.proto order.txtpb -m Order
text -> binary: wrote order.pb
```

- **No `protoc` install needed.** The `.proto` is compiled in-process by the
  `protoc` bundled with [`grpcio-tools`](https://pypi.org/project/grpcio-tools/),
  including its imports and the well-known types (`google/protobuf/*.proto`).
- **No generated code.** Message classes are built at runtime from the schema.
- **Direction is automatic.** Text in → binary out, binary in → text out
  (override with `--from` / `--to`).
- **Friendly message lookup.** `-m acme.v1.Order`, `-m .acme.v1.Order`,
  `-m v1.Order` and `-m Order` all work, as do nested types (`-m Order.Note`) and
  types from imported files.
- **Lossless, deterministic output.** Binary output is serialized
  deterministically (stable map ordering), so round trips produce identical bytes.
- **Catches the wrong schema.** It warns when binary input contains fields the
  chosen message doesn't define, which usually means the wrong `--message` or an
  out-of-date `.proto`.

---

## Contents

- [Why a schema is required](#why-a-schema-is-required)
- [Installation](#installation)
- [Command-line usage](#command-line-usage)
  - [Options](#options)
  - [Choosing the message type](#choosing-the-message-type)
  - [Input format detection](#input-format-detection)
  - [Output location](#output-location)
  - [Imports and include paths](#imports-and-include-paths)
  - [Pipes, stdin and stdout](#pipes-stdin-and-stdout)
  - [Exit codes and messages](#exit-codes-and-messages)
- [Python API](#python-api)
- [Development](#development)
  - [Project layout](#project-layout)
  - [Running the tests](#running-the-tests)
  - [Linting and formatting](#linting-and-formatting)
  - [Building](#building)
  - [Continuous integration](#continuous-integration)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Why a schema is required

The binary wire format stores only field *numbers* and wire types, not field
names or message types. To turn bytes into readable text (or text into bytes),
pbconvert needs the `.proto` that defines the message and needs to know *which*
message in it the data is. When the file defines exactly one top-level message,
that message is used automatically. Otherwise pass `-m/--message`.

## Installation

Requires **Python 3.10+**. Dependencies are installed automatically:
`protobuf` 4.25 or newer and `grpcio-tools` 1.62 or newer. Both the oldest
supported versions and the latest ones are tested in CI.

### With pipx (recommended for the command-line tool)

```bash
pipx install git+https://github.com/jnaujok/pbconvert.git
pbconvert --version
```

### With pip

```bash
pip install git+https://github.com/jnaujok/pbconvert.git
```

### From a built wheel

Every CI run uploads the sdist and wheel as the `dist` artifact. Download it,
then run:

```bash
pip install pbconvert-1.0.0-py3-none-any.whl
```

### From source with Poetry

```bash
git clone https://github.com/jnaujok/pbconvert.git
cd pbconvert
poetry install          # creates a virtualenv with pbconvert + dev tools
poetry run pbconvert --help
```

You can also run it as a module: `python -m pbconvert ...`.

## Command-line usage

```text
pbconvert PROTO INPUT [-o OUTPUT] [-m MESSAGE] [-I DIR ...] [--from FMT] [--to FMT]
```

### Options

| Option | Description |
| --- | --- |
| `PROTO` | The `.proto` file that defines the message. |
| `INPUT` | Input file, binary or text. Use `-` for stdin. |
| `-o, --output FILE` | Output file. `-` writes to stdout. Defaults to the input path with its extension swapped (see [Output location](#output-location)). |
| `-m, --message NAME` | Message type. Required when the `.proto` has more than one top-level message. See [Choosing the message type](#choosing-the-message-type). |
| `-I, --include DIR` | Extra import search path for the `.proto`. Repeatable. |
| `--from {binary,text}` | Input format. Default: detect it. |
| `--to {binary,text}` | Output format. Default: the opposite of the input format. |
| `--version` | Print the version and exit. |
| `-h, --help` | Show help with examples. |

### Choosing the message type

Given this schema:

```protobuf
syntax = "proto3";
package acme.v1;

import "sub/common.proto";              // defines acme.common.Money

message Item  { string sku = 1; acme.common.Money price = 2; }
message Order {
  int64 id = 1;
  repeated Item items = 2;
  message Note { string text = 1; }
  Note note = 4;
}
```

`-m` accepts any of these forms:

| You pass | Resolves to | Why |
| --- | --- | --- |
| `acme.v1.Order` | `acme.v1.Order` | fully qualified |
| `.acme.v1.Order` | `acme.v1.Order` | leading dot (as written in descriptors) is ignored |
| `v1.Order`, `Order` | `acme.v1.Order` | unambiguous dotted suffix |
| `Order.Note`, `Note` | `acme.v1.Order.Note` | nested types are searchable |
| `Money` | `acme.common.Money` | types from imported files are searched too |

Lookup rules:

1. An exact fully-qualified match always wins.
2. Otherwise, the name is matched against whole trailing components of each
   message's full name: `Order` matches `acme.v1.Order`, but `rder` does not.
   Matching is case-sensitive.
3. Messages in the file you passed are searched before messages in its imports.
   If `main.proto` defines `Widget` and imports a different `dep.Widget`,
   `-m Widget` picks `main.Widget`.
4. If a short name matches more than one message, pbconvert stops and lists the
   candidates so you can qualify it:

   ```text
   error: Message 'A' is ambiguous; use one of: amb.A, amb.B.A
   ```

5. If nothing matches, the error lists every message defined in the file,
   nested ones included, by fully-qualified name.

Without `-m`, the file must have exactly one **top-level** message. Nested types
don't count.

### Input format detection

1. `--from text|binary`, if given. No fallback: if the input isn't in that
   format, it's an error.
2. Otherwise the file extension (case-insensitive):
   - text: `.txtpb`, `.textproto`, `.pbtxt`, `.prototxt`, `.txt`
   - binary: `.pb`, `.bin`, `.binpb`
3. Otherwise (unknown extension, or stdin): try parsing as text, and if that
   fails, as binary.

### Output location

- `-o FILE` writes to `FILE`. `-o -` writes raw bytes to stdout.
- Without `-o`, the input's extension is replaced with `.txtpb` (text output) or
  `.pb` (binary output), next to the input file.
- pbconvert refuses to overwrite its own input. For example,
  `pbconvert x.proto a.txtpb --to text` would target `a.txtpb` again, so it
  asks for `-o`.
- A one-line summary (`binary -> text: wrote order.txtpb`) goes to stderr,
  except when writing to stdout.

### Imports and include paths

The directory containing `PROTO` is always on the import path, and so are the
well-known types (`google/protobuf/timestamp.proto`, `any.proto`,
`struct.proto`, ...). For imports that live elsewhere, add `-I`:

```bash
# orders.proto does: import "vendor/ext.proto";
pbconvert protos/orders.proto order.pb -m Order -I third_party/protos
```

### Pipes, stdin and stdout

```bash
# binary on stdin -> text on stdout
cat order.pb | pbconvert orders.proto - -m Order --from binary -o -

# pretty-print / normalize a text file in place via a temp file
pbconvert orders.proto messy.txtpb -m Order --to text -o clean.txtpb

# inspect a binary blob from another tool
some-tool --dump | pbconvert orders.proto - -m Order -o - | less
```

Reading from stdin requires `-o`, because there is no input filename to derive
an output name from.

### Exit codes and messages

| Code | Meaning |
| --- | --- |
| `0` | Success. Warnings may still have been printed. |
| `1` | Conversion error: missing file, `.proto` doesn't compile, unknown or ambiguous message, invalid input, would overwrite input. |
| `2` | Invalid command-line usage (argparse). |

All diagnostics go to **stderr**, prefixed with `error:` or `warning:`, so
stdout stays clean for piping. protoc's own compiler errors are printed as-is
just above the `error: protoc failed ...` line.

## Python API

Everything the CLI does is available as a library:

```python
from pathlib import Path
import pbconvert

# One-shot conversion
src, dst, payload = pbconvert.convert(
    "orders.proto",
    Path("order.pb").read_bytes(),
    message="Order",  # same lookup rules as -m
    includes=["third_party"],  # extra -I paths
    src="binary",  # or None to detect (uses `path` suffix if given)
    dst="text",  # or None for "the opposite"
)
print(payload.decode())

# Lower-level building blocks
pool, file_desc, file_names = pbconvert.load_proto("orders.proto")
desc = pbconvert.pick_message(pool, file_desc, "Order.Note", file_names)
Note = pbconvert.message_class(pool, desc)

msg = pbconvert.parse_text(Note, b'text: "hi"')
data = pbconvert.serialize(msg, "binary")  # deterministic bytes
again = pbconvert.parse_binary(Note, data, warn=None)  # warn=None silences warnings
```

| Function | Purpose |
| --- | --- |
| `load_proto(proto, includes=())` | Compile a `.proto` into a private `DescriptorPool`. Returns `(pool, file_descriptor, file_names)`. |
| `pick_message(pool, file_desc, name, file_names=())` | Resolve a message name (rules above). |
| `all_messages(pool, file_names)` | List every message descriptor (including nested) in the given files. |
| `message_class(pool, desc)` | Build a concrete message class from a descriptor. |
| `parse_text(cls, data)` / `parse_binary(cls, data, warn=True)` | Parse bytes into a message. `warn` may be `True` (stderr), a stream, or `False`/`None`. |
| `detect_format(path, forced, cls, data)` | Parse with format detection. Returns `(format, message)`. |
| `serialize(msg, fmt)` | Serialize to `"text"` or `"binary"`. |
| `convert(proto, data, *, message, includes, src, dst, path)` | All of the above in one call. Returns `(src, dst, payload)`. |

All user-facing failures raise `pbconvert.ConversionError`.

Each `load_proto` call uses its own descriptor pool, so loading several (even
conflicting) schemas in one process is safe.

## Development

### Project layout

```text
pbconvert/
├── pyproject.toml            # Poetry / PEP 621 metadata, tool config
├── poetry.lock
├── src/pbconvert/
│   ├── __init__.py           # public API, __version__
│   ├── __main__.py           # python -m pbconvert
│   ├── cli.py                # argparse front end (console script entry point)
│   └── core.py               # compile, resolve, parse, serialize
├── tests/
│   ├── conftest.py           # shared fixtures
│   ├── test_schema.py        # load_proto / pick_message / message_class
│   ├── test_convert.py       # parsing, detection, serialization, convert()
│   ├── test_cli.py           # end-to-end CLI behaviour
│   ├── protos/               # fixture schemas
│   └── protos_extra/         # fixture schemas reachable only via -I
└── .github/workflows/ci.yml  # lint, test matrix, build
```

### Running the tests

```bash
poetry install
poetry run pytest                       # full suite
poetry run pytest --cov                 # with coverage (branch coverage, missing lines)
poetry run pytest tests/test_cli.py -k stdin -v
```

The suite covers message lookup (qualified, suffix, nested, imported,
ambiguous, not found), schema compilation (imports, `-I`, syntax errors),
text and binary parsing (malformed input, unknown-field warnings), format
detection for every recognized extension, deterministic serialization,
lossless round trips, and the CLI end to end (default output naming,
overwrite protection, stdin/stdout, exit codes, `python -m pbconvert`).

### Linting and formatting

```bash
poetry run ruff check .          # lint
poetry run ruff format .         # format (CI runs --check)
poetry run pyright               # type check (the same engine as VS Code's Pylance)
```

### Building

```bash
poetry build                     # -> dist/pbconvert-<ver>.tar.gz and .whl
```

The wheel installs a `pbconvert` console script (`pbconvert.cli:main`).

### Continuous integration

`.github/workflows/ci.yml` runs on pushes, pull requests and manual dispatch:

1. **Lint**: `poetry check --lock`, `ruff check`, `ruff format --check`,
   `pyright`.
2. **Test**: pytest with coverage (must be at least 95%) on Python 3.10–3.14
   on Ubuntu, plus Python 3.13 on macOS and Windows.
3. **Test (minimum dependencies)**: installs the oldest supported versions
   (Python 3.10, protobuf 4.25, grpcio-tools 1.62) with plain pip and runs
   the suite, so the version floors in `pyproject.toml` stay honest.
4. **Build**: runs after lint and both test jobs pass. It builds the sdist and wheel,
   installs the wheel into a clean virtualenv, does a text → binary → text
   round trip with the installed `pbconvert` script, and uploads `dist/` as a
   workflow artifact.

## Troubleshooting

**`error: Message 'X' not found. Available: ...`**
Copy one of the listed names. Names are case-sensitive. If the type lives in a
file that isn't imported by `PROTO`, point `PROTO` at that file instead.

**`error: The proto has N top-level messages; choose one with --message.`**
Pass `-m` with one of the listed names.

**`warning: input has fields not in the .proto ...`**
The bytes decoded, but some field numbers aren't defined in the chosen message.
Those fields are dropped from the text output. Check that `-m` names the right
type and that your `.proto` is current. Binary data often decodes "successfully"
as the wrong type, so take this warning seriously.

**`error: Not valid protobuf binary for this message: ...`**
The input is truncated, corrupt, not protobuf, or a different message type.
If the file is actually text, pass `--from text`.

**`error: Not valid protobuf text for this message: 1:1 : ... has no field named ...`**
The text doesn't match the message. Usually this means the wrong `-m`. Field
names in text format are the `.proto` field names, not JSON camelCase names.

**`error: protoc failed to compile the .proto file (see above)`**
Read protoc's message just above it. For `File not found` on an import, add the
directory that contains the import root with `-I`.

**`error: output would overwrite the input; use -o`**
Your `--to` matches the input's format, so the default output name is the
input itself. Choose an output name with `-o`.

**Pylance or Pyright reports `Import "google.protobuf" could not be resolved`**
VS Code is using a different interpreter from the one you installed
pbconvert into. Run `poetry install`, then choose **Python: Select
Interpreter** and pick `.venv/bin/python` (`.venv\Scripts\python.exe` on
Windows). The repository's `poetry.toml` makes Poetry create that `.venv` in
the project, and `pyproject.toml` points Pyright at it.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
