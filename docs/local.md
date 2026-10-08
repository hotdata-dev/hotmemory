# Run against a local RuntimeDB

RuntimeDB is the Hotdata query engine. The library can use a local RuntimeDB in place of a
cloud workspace, with no API key and no cloud service. This page tells you how to start it
and how to point `HotdataStore` at it. The integration tests and the measurement scripts
also use the stack.

## Start the stack

You need Docker Desktop. Run this command from the repository root:

```sh
make local-up
```

The target starts three containers from [compose.yaml](../compose.yaml):

| Service | Image | Purpose |
|---|---|---|
| `catalog` | `postgres:17` | Holds the RuntimeDB catalog and the DuckLake metadata of each managed table. |
| `storage` | `rustfs/rustfs:latest` | S3-compatible object storage for the table data and for uploads. |
| `runtimedb` | `ghcr.io/hotdata-dev/runtimedb:latest` | The engine, on port 3000. |

The target creates the `runtimedb` bucket in storage, starts the engine, and waits until
`GET /health` answers. To stop the stack and delete all of its data, run `make local-down`.
To download newer images, run `make local-pull`.

To use a different port or engine image, set the make variables:

```sh
make local-up LOCAL_PORT=3100 RUNTIMEDB_IMAGE=runtimedb:local
```

Port 9000 on the host must be free, because storage always uses it.

Warning: do not expose this stack to a network. The engine accepts every request with no
token, and the storage credentials are fixed and public.

## Why three containers

A managed table needs two services that the bare engine image does not have.

- A Postgres catalog. RuntimeDB keeps the DuckLake metadata of a managed table in Postgres.
  DuckLake metadata records which parquet files make up each version of the table. With
  a Postgres `[catalog]`, the engine derives the DuckLake address from it. The bare image
  uses a SQLite catalog, so it refuses to create a managed database with
  `managed catalogs require ducklake.metadata_pg_url to be configured`.
- Object storage that can presign. The framework uploads parquet through presigned URLs.
  A presigned URL is a storage address, signed by the engine, to which the client sends
  the file directly. Filesystem storage cannot presign, and a load from the bare image
  fails with `PRESIGN_UNSUPPORTED`.

A presigned URL carries the host name of the storage endpoint. The engine and the client
on the host must reach the same name. The `runtimedb` service shares the network namespace
of `storage`, so `127.0.0.1:9000` is the storage service both inside the engine and on the
host.

## Point the library at it

Set these three variables in the shell that runs the library:

```sh
export HOTDATA_API_URL=http://localhost:3000
export HOTDATA_WORKSPACE=local
export HOTDATA_API_KEY=local
```

- `HOTDATA_API_URL` sends each API call to the local engine.
- `HOTDATA_WORKSPACE` can be any value. If it is set, the framework does not ask for the
  workspace list, which the local engine does not serve.
- `HOTDATA_API_KEY` can be any value that is not empty. The engine does not read it, but
  `HotdataClient.from_env()` in `hotdata-framework` refuses an empty key. This statement comes from the framework source.

Then `HotdataStore.provision` creates or opens a database on the local engine. This
example uses a toy embedder, so it needs no model:

```python
from hotmemory.hotdata import HotdataStore


def embed(texts):
    return [[text.count("disk") + 0.1, text.count("cpu") + 0.1] for text in texts]


store = HotdataStore.provision("local-memory", embedder=embed, model="toy", dimensions=2)
store.put(("team", "alerts"), "disk", kind="fact", content="The disk fills at night.")
print(store.get(("team", "alerts"), "disk").id)  # team/alerts/disk@1
```

`make integration` runs the tests marked `hotdata` and the conformance suite against the
stack. It sets `HOTMEMORY_TEST_URL` to `http://localhost:3000`, and needs none of the
variables above. To run the tests against another engine, set `HOTMEMORY_TEST_URL`.

`scripts/measure_local.py` needs none of these. It reads `HOTMEMORY_LOCAL_URL`, which
defaults to `http://localhost:3000`.

## What works locally

Measurement M6 in [guarantees.md](guarantees.md) records each call. On 2026-10-05, every
call that the driver needs worked against this stack, except one. A provider-backed vector
index fails with `Embedding provider 'sys_emb_openai' not found`, because the stack
configures no embedding provider. A plain vector index, over embeddings that the caller
supplies, works.
