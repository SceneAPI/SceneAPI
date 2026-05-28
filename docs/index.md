# sfmapi

**An HTTP API for running Structure-from-Motion workflows without tying
clients to one SfM engine.**

sfmapi defines the server contract, long-running job model, progress
streaming, chunked uploads that finalize into content-addressed blobs,
sealed reconstruction snapshots, and SDK-facing endpoints. Concrete SfM
engines live in backend packages and register with the server at
startup.

## Core idea

Client applications call the sfmapi REST API. The server records
long-running work as jobs and tasks, workers drive a registered backend
engine, and task runners seal readable reconstruction outputs into
durable snapshots.

## Choose your path

::::{grid} 2
:gutter: 3

:::{grid-item-card} Try it quickly
Run a local server with SQLite, filesystem blobs, and an in-process
worker.

- {doc}`Quickstart <guides/quickstart>`
- {doc}`First REST workflow with curl <reference/curl_tour>`
:::

:::{grid-item-card} Call the API
Create resources, submit long-running jobs, watch progress, and read
sealed reconstruction snapshot files. Inspect backend-native action
catalogs and backend option schemas when a deployment exposes
engine-specific tools or provider-specific stage options.

- {doc}`REST API reference <reference/api>`
- {doc}`OpenAPI <reference/openapi>`
- {doc}`MCP adapter <guides/mcp>`
:::

:::{grid-item-card} Use a client library
Use generated Python/TypeScript surfaces plus the header-only C++17
client, all checked against the same wire fixtures.

- {doc}`SDK overview <sdk/index>`
:::

:::{grid-item-card} Build or operate
Implement the smallest backend protocol that fits, advertise portable
capabilities only for supported stages, expose backend-native actions
and config schemas, deploy web/worker tiers, and configure storage and
auth.

- {doc}`Implement a backend <guides/backend_implementations>`
- {doc}`Deployment <guides/deployment>`
:::

:::{grid-item-card} Specification and releases
Look up the normative contract and project-level release history.

- {doc}`SFMAPI specification <spec>`
- {doc}`Changelog <changelog>`
- {doc}`Release policy <guides/release_policy>`
:::

::::

## More entry points

- {doc}`Authentication <reference/auth>` and {doc}`error handling <reference/errors>`
  for production callers.
- {doc}`Storage <guides/storage>` and {doc}`jobs and progress <guides/jobs_and_progress>`
  for backend and operator context.
- {doc}`Plugin hub checklist <guides/plugin_hub_checklist>` for the
  multi-provider registry, CLI, admin API, and backend app contract.
- {doc}`Configuration <reference/configuration>`, {doc}`multi-tenancy <guides/multitenancy>`,
  and {doc}`CLI/scripts <reference/cli>` for deployments.
- {doc}`Changelog <changelog>` for release history and project updates.

## Status

Pre-release. API shapes may change before 1.0. The current tree ships
the REST server, Python/TypeScript/C++ clients, SQLite and Postgres
support, and CI coverage for the wire contract.

This repository ships no concrete SfM backend on purpose; it is the
contract. Backend implementations such as pycolmap, OpenSfM, hloc, or
custom forks live in their own packages.

```{toctree}
:caption: Start here
:hidden:
:maxdepth: 2

guides/quickstart
First REST workflow with curl <reference/curl_tour>
```

```{toctree}
:caption: Use the API
:hidden:
:maxdepth: 2

reference/api
reference/openapi
reference/job_configuration
guides/mcp
reference/auth
reference/errors
```

```{toctree}
:caption: Build backends
:hidden:
:maxdepth: 2

guides/backend_implementations
guides/plugin_hub_checklist
guides/container_plugin_runtime_checklist
guides/plugin_runtime_api_gap_closure_checklist
guides/architecture
guides/storage
guides/jobs_and_progress
```

```{toctree}
:caption: Operate sfmapi
:hidden:
:maxdepth: 2

guides/deployment
reference/configuration
guides/multitenancy
reference/cli
```

```{toctree}
:caption: SDKs and clients
:hidden:
:maxdepth: 2

SDK overview <sdk/index>
```

```{toctree}
:caption: Specification
:hidden:
:maxdepth: 1

spec
```

```{toctree}
:caption: Contribute and internals
:hidden:
:maxdepth: 1

guides/contributing
guides/release_policy
server/orchestrator
server/storage
server/workers
server/adapters
server/services
changelog
GitHub repository <https://github.com/sfmapi/sfmapi>
```
