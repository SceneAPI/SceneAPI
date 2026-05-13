# Release policy

sfmapi is pre-1.0. Public package and wire contracts may still change,
but release mechanics should stay predictable.

## Version alignment

Official repositories start at `0.0.1`: `sfmapi`, `sfmapi-sdk`,
`sfmapi-bench`, and the backend plugin repos. Until `0.1.0`, bump all
official packages together when the wire contract, plugin manifest
contract, or shared backend SDK surface changes. Backend-only fixes may
use a patch release in that backend repo without forcing a core release.

Tags use `vX.Y.Z`. Release workflows fail when the tag version does not
match the package version in `pyproject.toml` or `package.json`.

## Publishing

`sfmapi` publishes the Python package to PyPI and the web image to GHCR.
`sfmapi-sdk` publishes the Python packages and the TypeScript package.
Backend repos publish Python packages. PyPI publishing uses trusted
publishing through GitHub OIDC; npm publishing requires `NPM_TOKEN`.

## Validation tiers

Push CI is lightweight and must not require licensed tools, GPUs, or
large datasets. Scheduled/manual workflows cover plugin installation,
real-data benchmarks, native tool checks, and external conformance
targets.

The hub install workflow needs `SFMAPI_HUB_INSTALL_TOKEN` when official
plugin repos are private. Use a fine-grained token with read-only access
to the plugin repositories. Without it, the workflow validates bundled
manifests but skips GitHub install and entry-point checks.

## Branch policy

`main` is protected in GitHub. Required checks are the normal push CI
jobs for each repo. Long-running scheduled/manual jobs report health but
are not merge gates.
