# Container Plugin Runtime Checklist

This checklist tracks implementation of container-backed backend plugins. The
API contract is additive: existing `uv`, `docker`, and `external_tool` runtime
modes remain valid, while `container_service` represents an already-running
plugin service that sfmapi can call over a versioned protocol.

The API-review gap closure plan is tracked separately in
`docs/guides/plugin_runtime_api_gap_closure_checklist.md`. Close its P0 items
before treating `container_service` or plugin provisioning as production-ready.

## Contract Design

- [x] Add `container_service` as an optional runtime mode in `sfm_hub.models`.
- [x] Keep `docker` as image/build planning only until a local container runner
  exists.
- [x] Require `protocol="sfmapi-plugin-http-v1"` and `protocol_version`.
- [x] Require a service endpoint through `default_url` or `url_env`.
- [x] Add JSON schema coverage for `runtime_modes.container_service`.
- [x] Add typed image/build metadata and digest fields for plugins that publish
  containers.
- [x] Add typed execution, mount, GPU, env, secret, timeout, retry, shutdown,
  log, and artifact-collection specs.
- [x] Add digest/image provenance, cache policy, and object-store specs.
- [ ] Add protocol compatibility rules for future `sfmapi-plugin-http-v2`.

## Plugin Service Protocol

- [ ] Implement a reusable plugin service adapter that exposes one backend
  object over HTTP.
- [ ] Implement `GET /healthz` returning service health.
- [ ] Implement `GET /version` returning `protocol`,
  `protocol_version`, plugin id, package version, backend version, and optional
  capabilities hash. `sfmapi-plugin-http-v1` compatibility is major-version
  based.
- [ ] Implement `GET /capabilities`.
- [ ] Implement `GET /actions`.
- [ ] Implement `POST /actions/{action_id}:validate`.
- [x] Define the bridge execution endpoint as `POST /execute` with
  `request_id`, `plugin_id`, `provider`, `action_id`, `inputs`, mounted IO
  roles, redacted env/secret key names, logs, and artifacts.
- [x] Include optional image/build metadata, object-store configuration, cache
  policy, and provenance in the `/execute` request so services see the same
  runtime contract the manifest declared.
- [ ] Implement the reusable plugin-server `POST /actions/{action_id}:run`
  endpoint.
- [ ] Implement `GET /jobs/{job_id}`.
- [ ] Implement `POST /jobs/{job_id}:cancel`.
- [ ] Implement `GET /jobs/{job_id}/events`.
- [ ] Implement artifact download/manifest endpoints.

## Runtime Integration

- [x] Add install-plan support for recording `container_service` mode.
- [x] Add doctor support for `default_url` / `url_env` endpoint resolution and
  health checks.
- [x] Bridge replay can execute backend-action jobs through a configured
  `container_service` `/execute` endpoint.
- [ ] Implement reusable `ContainerBackendProxy` for all provider routing.
- [ ] Implement `ContainerRuntimeManager` for service health, logs, actions,
  cancellation, and cleanup.
- [ ] Map container progress events to `ProgressReporter`.
- [ ] Map protocol errors to sfmapi `ValidationError`,
  `CapabilityUnavailableError`, and job failures.
- [ ] Add deployment-config endpoint resolution beyond manifest `url_env` /
  `default_url`.

## Security And Operations

- [ ] Reject host absolute paths in container action payloads.
- [ ] Use fixed mount roles: `/workspace`, `/inputs`, `/outputs`, `/cache`.
- [ ] Enforce read-only inputs and write-scoped outputs/cache/workspace.
- [ ] Redact secret env vars from API, MCP, doctor, and logs.
- [x] Require internal-network-only service exposure by default. The current
  C++ validator probes `container_service` endpoints over plain HTTP on the
  private service network; terminate TLS/auth at ingress or sidecar boundaries
  and pass the API an internal `http://` URL.
- [ ] Add per-service auth token or mTLS.
- [ ] Require non-root images and no Docker socket mounts for production docs.
- [ ] Add structured logs, metrics, liveness/readiness, and restart behavior.

## Authoring And Release

- [ ] Add scaffold templates: `Dockerfile`, `.dockerignore`, entrypoint,
  compose smoke file, protocol tests, and image CI workflow.
- [ ] Publish images to GHCR with immutable digests.
- [ ] Attach SBOM/provenance and vulnerability scan results.
- [ ] Update manifests only after image smoke and protocol conformance pass.
- [ ] Start with an `echo_container` reference plugin before heavy engines.
- [ ] Migrate plugins in order: `pycolmap`, `colmap_cli`, `hloc`,
  `instantsfm`, `spheresfm`.

## Verification

- [x] Manifest validation rejects malformed `container_service` endpoints.
- [x] `sfmapi plugins install <plugin> --method container_service --dry-run`
  returns no shell command and a doctor warning.
- [x] `sfmapi plugins doctor <plugin>` checks service health and reports the
  configured protocol version when `container_service` is configured.
- [x] `sfmapi plugins doctor <plugin>` rejects remote services that report an
  incompatible protocol version.
- [ ] REST API, Python SDK, MCP HTTP, and MCP stdio can discover the proxied
  provider.
- [x] C++ generated plugin registry/detail payloads are regenerated after
  manifest changes.
- [x] C++ bridge replay validates mounted output IO and exposes stable
  `/v1/jobs/{job_id}/artifacts` metadata in
  `parity/e2e_container_service.py`.
- [x] Container-service e2e verifies image/build, object-store, cache, and
  provenance metadata are present in the `/execute` request.
- [ ] Bicycle `images_2` e2e passes through API and MCP entrypoints.
- [x] Negative tests cover plugin down, bad health, bad protocol version,
  cancellation, timeout, and restart. Invalid artifact, denied cache, and
  missing GPU remain follow-ups.

## Local Checks

```bash
uv run pytest tests/unit/test_plugin_hub.py tests/unit/test_plugin_api.py tests/unit/test_plugin_cli.py -q
uv run ruff check sfm_hub app/cli.py app/schemas/api/plugins.py app/services/plugin_service.py tests/unit/test_plugin_hub.py
uv run python -m bench.cli plugins
```
