# Run against a local RuntimeDB

RuntimeDB is the Hotdata query engine. It runs from a container image on a laptop, with a
SQLite catalog and filesystem storage, and with no cloud service. This page tells you how
to start it and how to point the library at it. The bare image does not serve managed
tables yet. The last section gives the details.

## Start the container

You need Docker Desktop. Run this command from the repository root:

```sh
make local-up
```

The target pulls `ghcr.io/hotdata-dev/runtimedb:latest` and starts a container named
`hotmemory-runtimedb` on port 3000. Then it waits until `GET /health` answers. The target
sets these container variables:

| Variable | Value | Purpose |
|---|---|---|
| `RUNTIMEDB_SECRET_KEY` | a random 32-byte key | Encrypts stored credentials. The image refuses to start without it. |
| `RUNTIMEDB_AUTH__ALLOW_UNAUTHENTICATED` | `true` | Accepts requests with no token. The image refuses to start without a choice of authentication mode. |
| `RUNTIMEDB_ENGINE__SQL_WRITES` | `true` | Allows INSERT and CREATE TABLE statements. Measurement M4 needs it. Production does not set it. |

This is the same command as in the RuntimeDB README, with the SQL writes flag added:

```sh
docker run -d --rm --name hotmemory-runtimedb -p 3000:3000 \
  -e RUNTIMEDB_SECRET_KEY="$(openssl rand -base64 32)" \
  -e RUNTIMEDB_AUTH__ALLOW_UNAUTHENTICATED=true \
  -e RUNTIMEDB_ENGINE__SQL_WRITES=true \
  ghcr.io/hotdata-dev/runtimedb:latest
```

To use a different port or image, set the make variables:

```sh
make local-up LOCAL_PORT=3100 RUNTIMEDB_IMAGE=runtimedb:local
```

The container keeps no data after it stops. To stop it, run `make local-down`.

Warning: do not expose this container to a network. It accepts every request with no
token.

## Point the library at it

Set these three variables in the shell that runs the library or a script:

```sh
export HOTDATA_API_URL=http://localhost:3000
export HOTDATA_WORKSPACE=local
export HOTDATA_API_KEY=local
```

- `HOTDATA_API_URL` sends each API call to the container.
- `HOTDATA_WORKSPACE` can be any value. If it is set, the framework does not ask for the
  workspace list, which a bare container does not serve.
- `HOTDATA_API_KEY` can be any value that is not empty. The container does not read it,
  but `HotdataClient.from_env()` in `hotdata-framework` refuses an empty key. This
  statement comes from the framework source and was not yet observed against a container.

## What works locally

The bare container answers `GET /health`. It refuses a query that names no database. It
refuses to create a managed database, with this error:

```text
managed catalogs require `ducklake.metadata_pg_url` to be configured
```

This result is from 2026-10-05, with the `latest` image pulled on that day
(digest `sha256:302371bb1923`). The library writes
only to managed tables, so it cannot use the bare container. A managed table needs a
Postgres database for the DuckLake metadata. Measurement M6 in
[guarantees.md](guarantees.md) records each call. A provider-backed vector index also
needs an embedding provider, and the bare container configures none.
