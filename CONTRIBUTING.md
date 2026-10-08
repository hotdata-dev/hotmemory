# Contributing

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installs the development tools and runs the scripts.
- GNU Make runs the targets below.
- Docker Desktop runs the local RuntimeDB stack. You need it only for `make local-up`.

## The one command

Run this command before each commit:

```sh
make verify
```

`make verify` is the full check. CI runs the same target. If it passes on your machine, it
passes in CI. It has no tiers, because the full check takes less than five seconds.

`make verify` runs these checks, in this order:

1. `ruff check` over the Python files.
2. `ruff format --check` over the Python files and the Python code blocks in Markdown.
3. Strict `mypy` over `src/` and `tests/`.
4. The offline test suite, with `pytest --disable-socket`. A test that opens a network
   socket fails.
5. The link check. It reads each relative link in the Markdown files at the root and
   under `docs/`. A link to a file that does not exist makes it fail.

## The other targets

| Target | What it does |
|---|---|
| `make verify` | Runs every check above. |
| `make integration` | Runs the tests marked `hotdata` against the local stack. Start the stack first with `make local-up`. To use another engine, set `HOTMEMORY_TEST_URL`. |
| `make local-up` | Starts the local RuntimeDB stack: Postgres, RustFS, and the engine. [docs/local.md](docs/local.md) tells you how to point the library at it. |
| `make local-down` | Stops the stack and deletes its data. |
| `make local-pull` | Downloads newer images for the stack. |

## Rules of the harness

The library is deterministic. The offline suite needs no real clock, no network, and no
model. The tests pass a fixed clock and a fake embedder to the store, from
`tests/conftest.py`. These rules keep the suite deterministic.

- One conformance suite, `tests/test_conformance.py`, runs against every driver through
  the `store` fixture. Each answered guarantee of the storage contract in
  [docs/guarantees.md](docs/guarantees.md) has its tests there. A driver that fails a
  conformance test is not a driver. To add a driver, add it to `DRIVERS` in
  `tests/conftest.py`.
- The in-memory driver is the reference for the Hotdata driver. From phase 2, a test
  builds the same records in both drivers and compares the results of `search`.
- Four surfaces are frozen: the names in `__all__`, the method set of the `Store`
  protocol, the fields and field types of the record for each schema version, and the
  filter keys of `list` and `search`. A test compares each surface against a literal set.
  These tests are in `tests/test_frozen.py`. A change to a frozen surface is a public
  contract change, and it needs an entry in [CHANGELOG.md](CHANGELOG.md).
- A frozen surface is compared against a literal set, never against the thing that it
  protects. A test that iterates over the protected thing turns a deletion into one test
  fewer and not into a failure.
- Each row in the guarantees ledger names the conformance tests that prove it, or the phase
  that will prove it. `tests/test_ledger.py` reads the ledger. A named test that the
  conformance suite does not define makes it fail. A row with no test and no phase also
  makes it fail.
- Tests marked `hotdata` run the Hotdata driver against a running engine, and the
  conformance suite runs against it as the `hotdata` driver. They need
  `HOTMEMORY_TEST_URL` to name the engine. Without it, they skip, so `make verify` stays
  offline. `make integration` sets it to the local stack. A run provisions one database
  with a new name, empties its tables after each test, and deletes it at the end. They are
  the only tests that use the network.
- CI runs two jobs on each pull request. `verify` runs `make verify`. `integration` starts
  the local stack with `make local-up` and runs `make integration`.
- No test calls a model. The tests pass a fake embedder. From phase 3, `capture` takes a
  callable, and the tests pass a fake extractor that returns fixed facts.
- There is no coverage gate, no mutation-testing gate, and no report generator. The output
  of `make verify` is the report.

## Documents

Documents are checked like code. If you change a behavior, update
[docs/contracts.md](docs/contracts.md) and [docs/guarantees.md](docs/guarantees.md) in the
same pull request. If you change a public surface, add an entry to
[CHANGELOG.md](CHANGELOG.md).

No file in this repository names a private repository, a customer, or a deployment detail.
This rule includes `docs/internal/`.
