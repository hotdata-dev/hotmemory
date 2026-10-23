# Releasing

A release publishes one version of hotmemory to PyPI and creates a GitHub Release. Use
`./scripts/release.sh` for each release. Do not change the version, push a tag, or create a
GitHub Release by hand.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) runs the scripts and updates `uv.lock`.
- The [GitHub CLI](https://cli.github.com/), `gh`, opens the release pull request. Log in
  with `gh auth login`.
- You can push to `main` and push tags to this repository.

The publish uses PyPI trusted publishing. PyPI accepts the upload from the workflow
`publish.yml` in the GitHub environment `pypi`, and no API token exists. The section
[One-time setup](#one-time-setup) tells you how to configure it.

## Release a version

1. Make sure that `CHANGELOG.md` has the notes of the release under `## [Unreleased]`.
2. Prepare the release pull request. Give a bump kind, or the version itself:

   ```sh
   ./scripts/release.sh prepare minor   # or patch, major, or 0.2.0
   ```

   The script starts from the latest `main`. It sets the version in `pyproject.toml`,
   moves the Unreleased notes into a section for the version, updates `uv.lock`, and runs
   `make verify`. Then it pushes the branch `release/vX.Y.Z` and opens the pull request.
3. Merge the pull request after CI passes.
4. Publish from a clean checkout:

   ```sh
   ./scripts/release.sh publish
   ```

   The script tags the version on the latest `main` and pushes the tag `vX.Y.Z`.

## What the tag starts

The tag starts the workflow `publish.yml`. It runs three jobs in order:

1. `build` makes sure that the tagged commit is on `main`, that the tag matches the
   version in `pyproject.toml`, and that `CHANGELOG.md` has a section for the version. Then it builds the wheel and the sdist with
   `uv build` and checks them with `twine check --strict`.
2. `pypi` uploads the wheel and the sdist to PyPI. It runs in the environment `pypi`.
3. `github-release` creates the GitHub Release. The release notes are the changelog section
   of the version, and the wheel and the sdist are attached.

If `build` or `pypi` fails, no GitHub Release is created. A version that PyPI accepted
cannot be uploaded again. To fix a bad release, release a new version.

## The checks

- On a pull request that changes `pyproject.toml` or `CHANGELOG.md`, the workflow
  `check-release.yml` runs `scripts/changelog.py check`. If the version changed and
  `CHANGELOG.md` has no section for the new version, it fails.
- If the Unreleased section is empty, `release.sh prepare` stops.
- If the tag exists or the changelog section is missing, `release.sh publish` stops.
- If the tagged commit is not on `main`, or the tag and the version differ, the `build`
  job stops.

## What the package contains

The wheel contains `src/hotmemory` only. The sdist contains `src/hotmemory`, `README.md`,
`CHANGELOG.md`, and `LICENSE`. The benchmark runner in `bench/`, the skill in
`skills/hotmemory/`, the tests, and the documents stay in the repository. To see the
contents, run `uv build` and list the archives in `dist/`.

The README is the project description on PyPI. A relative link does not work on PyPI, so
the README links to repository files with absolute GitHub links. `make verify` makes sure
that each of these links names a file that exists.

## One-time setup

An owner of the GitHub repository and the PyPI project does these steps once, before the
first release.

1. On GitHub, open Settings, then Environments, and create the environment `pypi`. To
   require an approval before each upload, add required reviewers.
2. On PyPI, open Your account, then Publishing, and add a pending trusted publisher with
   these values:

   | Field | Value |
   |---|---|
   | PyPI project name | `hotmemory` |
   | Owner | `hotdata-dev` |
   | Repository name | `hotmemory` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   The first upload creates the project and makes the publisher permanent.
3. After the first release, add the other maintainers to the PyPI project.
