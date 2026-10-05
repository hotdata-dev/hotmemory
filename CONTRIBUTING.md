# Contributing

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installs the development tools and runs the scripts.
- GNU Make runs the targets below.
- Docker Desktop runs the local RuntimeDB container. You need it only for `make local-up`.

## The one command

Run this command before each commit:

```sh
make verify
```

`make verify` is the full check. CI runs the same target. If it passes on your machine, it
passes in CI. It has no tiers, because the full check takes less than five seconds.

Today, `make verify` runs these checks, in this order:

1. `ruff check` over the Python files.
2. `ruff format --check` over the Python files and the Python code blocks in Markdown.
3. The link check. It reads each relative link in `README.md`, in `CONTRIBUTING.md`, and
   under `docs/`. A link to a file that does not exist makes it fail.

When the library exists, strict `mypy` and the offline test suite join `make verify`
before the link check.

## The other targets

| Target | What it does |
|---|---|
| `make verify` | Runs every check above. |
| `make local-up` | Starts a local RuntimeDB container. [docs/local.md](docs/local.md) tells you how to point the library at it. |
| `make local-down` | Stops the local container. |

## Rules of the harness

The library is deterministic. The offline suite needs no clock, no network, and no model.
These rules keep it that way.

- One conformance suite runs against every driver. Each answered guarantee in
  [docs/guarantees.md](docs/guarantees.md) is one test. A driver that fails a conformance
  test is not a driver.
- The in-memory driver is the reference for the Hotdata driver. A test builds the same
  records in both drivers and compares the results of `search`.
- Four surfaces are frozen: the names in `__all__`, the method set of the `Store`
  protocol, the fields and field types of the record for each schema version, and the
  filter keys of `list` and `search`. A test compares each surface against a literal set.
  A change to a frozen surface is a public contract change, and it needs a changelog
  entry.
- A frozen surface is compared against a literal set, never against the thing that it
  protects. A test that iterates over the protected thing turns a deletion into one test
  fewer and not into a failure.
- Each row in the guarantees ledger names the test that proves it. A test reads the ledger.
  A named test that does not exist makes it fail.
- Tests marked `hotdata` run the Hotdata driver against a real database. They need
  `HOTMEMORY_TEST_DB` to name a throwaway database. Without it, they skip. They
  run once for each pull request. They are the only tests that use the network.
- No test calls a model. `capture` takes a callable, and the tests pass a fake extractor
  that returns fixed facts.
- There is no coverage gate, no mutation-testing gate, and no report generator. The output
  of `make verify` is the report.

## Documents

Documents are checked like code. If you change a behavior, update
[docs/contracts.md](docs/contracts.md) and [docs/guarantees.md](docs/guarantees.md) in the
same pull request.

No file in this repository names a private repository, a customer, or a deployment detail.
This rule includes `docs/internal/`.
