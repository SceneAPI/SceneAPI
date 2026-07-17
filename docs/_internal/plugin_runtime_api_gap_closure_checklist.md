# Plugin Runtime API Gap Closure Checklist

This checklist closes the gaps from the plugin runtime API review. Treat P0
items as release blockers for advertising `container_service` or provisioning
as supported public contracts.

## Definition Of Done

- [x] Pydantic models and `backend-plugin.schema.json` accept and reject the
  same manifest shapes.
- [x] `container_service` doctor checks verify a real remote protocol contract,
  not only HTTP reachability.
- [x] Provisioning output never exposes secret environment values through REST
  or CLI JSON.
- [x] Non-dry-run installs are retryable and leave durable state after partial
  success or provisioning failure.
- [x] Focused unit tests, API tests, CLI tests, MCP tests, registry validation,
  and docs build all pass.

## P0: Manifest Contract Alignment

- [x] Choose one source of truth for manifest schemas. Prefer generating
  `sfm_hub/schemas/backend-plugin.schema.json` from `sfm_hub.models` or add a
  parity test that compares required/default fields.
- [x] Align `UvRuntime.source`: either make it required in Pydantic or remove
  it from JSON Schema `required` because Pydantic defaults it to `"git"`.
- [x] Align `ContainerServiceRuntime.protocol` and `protocol_version`: either
  require them in Pydantic or remove them from JSON Schema `required` because
  Pydantic defaults them.
- [x] Harden `ContainerServiceEndpoint.default_url` validation. Reject
  `http://`, `https://`, missing host, whitespace, credentials, fragments, and
  unsupported schemes.
- [x] Add schema parity tests with these cases:
  - [x] Minimal valid `uv` runtime.
  - [x] Minimal valid `container_service` with `default_url`.
  - [x] Minimal valid `container_service` with `url_env`.
  - [x] Invalid empty service endpoint.
  - [x] Invalid URL with no host.
  - [x] Invalid lower-case `url_env`.
- [x] Run `uv run python -m json.tool sfm_hub/schemas/backend-plugin.schema.json`.

## P0: Container Protocol Health

- [x] Define the plugin HTTP protocol response shape:
  - [x] `GET /healthz` returns health status only.
  - [x] `GET /version` returns `protocol`, `protocol_version`, plugin id,
    package version, backend version, and optional capabilities hash.
  - [x] Document version compatibility rules for `sfmapi-plugin-http-v1`.
- [x] Update `doctor` to call both `/healthz` and `/version`.
- [x] Fail `doctor` when the remote `protocol` is not
  `sfmapi-plugin-http-v1`.
- [x] Fail `doctor` when the remote protocol version is incompatible with the
  manifest-declared version.
- [x] Return machine-readable doctor metadata for protocol mismatch instead of
  requiring clients to parse the detail string.
- [x] Document the current C++ transport boundary: service validation is
  plain HTTP on an internal container network. External TLS/auth belongs at an
  ingress or sidecar, and `https://` service URLs are not accepted by the C++
  validator in this build.
- [x] Add negative tests:
  - [x] Service down or unreachable health path.
  - [x] Health returns non-2xx.
  - [x] Health returns malformed JSON, if JSON is required.
  - [x] Version endpoint missing.
  - [x] Protocol mismatch.
  - [x] Protocol version mismatch.

## P0: Provisioning Secret Safety

- [x] Replace public provisioning `env` output with one of:
  - [x] `env_keys: list[str]` for values callers must set manually.
  - [x] `redacted_env: dict[str, str]` with every value redacted.
  - [x] `outputs: dict[str, str]` only for explicitly non-secret values.
- [x] Add a sensitive-key detector for names containing `TOKEN`, `SECRET`,
  `KEY`, `PASSWORD`, `CREDENTIAL`, or `AUTH`.
- [x] Reject or redact secret-looking keys returned from plugin provisioners.
- [x] Ensure `PluginInstallResponse` and CLI JSON never include raw secret
  values.
- [x] Add tests where a fake provisioner returns `SFMAPI_PLUGIN_TOKEN` and
  assert the raw value is absent from all serialized output.

## P1: Install Idempotency And State

- [x] Add optional `request_id` to `PluginInstallRequest` for non-dry-run
  installs. Validate UUID-like length and document retry behavior.
- [x] Extend plugin state with provisioning fields:
  - [x] `provision_runtime`.
  - [x] `provisioned`.
  - [x] `provisioning_status`: `not_requested`, `planned`, `running`,
    `succeeded`, or `failed`.
  - [x] `provisioning_error` as a redacted string or structured reason.
- [x] Record package install state before running provisioning, or write a
  partial install record on provisioning failure.
- [x] Make repeated installs with the same `request_id` return the same logical
  result without rerunning destructive setup.
- [x] Add tests for:
  - [x] `uv install` succeeds and provisioning fails.
  - [x] Retry after provisioning failure.
  - [x] Retry with same `request_id`.
  - [x] Retry with different `request_id`.
  - [x] `--no-provision-runtime` keeps `provisioning_status=not_requested`.

## P1: Container-Service Install Execution

- [x] Decide default behavior for non-dry-run `container_service` installs:
  require successful health/protocol validation before recording state.
- [x] Do not add a `skip_health_check` opt-out; non-dry-run
  `container_service` installs must verify health and protocol before recording
  state.
- [x] Persist the resolved endpoint source (`default_url` or `url_env`) without
  leaking secret-bearing URLs.
- [x] Add tests for:
  - [x] Non-dry-run install fails when service is down or incompatible.
  - [x] Non-dry-run install fails on protocol mismatch.
  - [x] Non-dry-run install records state after a healthy compatible service.
  - [x] Dry-run does not contact the service.

## P2: SDK, MCP, And E2E Coverage

- [x] Add or update SDK-facing types for `PluginInstallRequest`,
  `PluginInstallResponse`, provisioning results, and doctor checks.
- [ ] Add MCP tests proving plugin/provider discovery works after loading a
  `container_service` provider proxy.
- [x] Add MCP tests proving registered plugin/provider discovery, plugin
  doctor metadata, and redacted dry-run install planning through direct MCP
  tools and a real FastMCP client.
- [x] Add REST API tests for install, doctor, enable, disable, and provider
  listing with a synthetic container service.
- [ ] Run bicycle `images_2` e2e through:
  - [ ] REST API entrypoint.
  - [ ] Python SDK entrypoint.
  - [ ] MCP HTTP entrypoint.
  - [ ] MCP stdio entrypoint.
- [ ] Exercise feature-by-feature endpoints for all supported plugin features,
  including hloc ALIKED and retrieval/vocab-tree paths where advertised.

## P2: Generated Payloads And Documentation

- [x] Regenerate C++ plugin registry/detail payloads after manifest or schema
  changes.
- [ ] Update `docs/reference/api.md` with request/response examples for
  provisioning, `container_service`, and doctor protocol mismatch.
- [x] Update `docs/guides/backend_implementations.md` with provisioner return
  shape, redaction rules, and retry expectations.
- [x] Update `docs/_internal/container_plugin_runtime_checklist.md` when each gap
  here is closed.

## Evidence Log

- [x] `uv run pytest tests/unit/test_plugin_hub.py tests/unit/test_plugin_api.py
  tests/unit/test_plugin_cli.py tests/unit/test_mcp_tools.py -q` passed
  84 tests.
- [x] `uv run ruff check sfm_hub app/cli.py app/api/v1/admin.py
  app/schemas/api/plugins.py app/services/plugin_service.py app/mcp/server.py
  app/mcp/tools.py tests/unit/test_plugin_hub.py tests/unit/test_plugin_api.py
  tests/unit/test_plugin_cli.py tests/unit/test_mcp_tools.py` passed.
- [x] `uv run pytest tests/unit -q` passed 354 tests.
- [x] `uv run sphinx-build -b html docs docs/_build/html` passed with the
  three pre-existing toctree warnings for unrelated guide drafts.
- [x] MCP external-process smoke passed for both transports: HTTP listed plugin
  tools and returned a redacted `plan_plugin_install` result; stdio listed
  plugins through the FastMCP client transport.

## Validation Commands

```bash
uv run ruff check sfm_hub app/cli.py app/api/v1/admin.py app/schemas/api/plugins.py app/services/plugin_service.py tests/unit/test_plugin_hub.py tests/unit/test_plugin_api.py tests/unit/test_plugin_cli.py tests/unit/test_mcp_tools.py
uv run pytest tests/unit/test_plugin_hub.py tests/unit/test_plugin_api.py tests/unit/test_plugin_cli.py tests/unit/test_mcp_tools.py -q
uv run pytest tests/unit -q
uv run python -m json.tool sfm_hub/schemas/backend-plugin.schema.json
uv run python -m bench.cli plugins
uv run sphinx-build -b html docs docs/_build/html
```
