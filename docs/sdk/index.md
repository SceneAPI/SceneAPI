# SDKs

sfmapi ships three client surfaces that target the same `/v1` wire
contract. Python and TypeScript generated surfaces are derived from the
OpenAPI spec. The C++17 client is a maintained header-only wire library
and pluggable HTTP client checked against the same fixtures.

| Language | Current install path | Notes |
|---|---|---|
| Python generated SDK | `pip install ../sfmapi-sdk/python/sfmapi_client_gen` | Use `sfmapi_client_gen.Client` plus helpers in `sfmapi_client_gen._ergonomics`. |
| TypeScript | `cd ../sfmapi-sdk/typescript && npm install && npm run build` | Package metadata is under the SDK repository until the package is published. |
| C++17 | Add `../sfmapi-sdk/cpp/` as a header-only dependency | Bring your own HTTP transport and JSON parser. |

Wire fixtures in `tests/contract/fixtures/` are replayed through the
clients, so a server change must propagate across the client surfaces or
CI fails.

## Python generated SDK

```bash
pip install ../sfmapi-sdk/python/sfmapi_client_gen
```

```python
from sfmapi_client_gen import Client
from sfmapi_client_gen._ergonomics import parse_points_binary, wait_for_job

client = Client(base_url="http://localhost:8080")
```

The generated package contains endpoint modules under
`sfmapi_client_gen.api`, typed models under `sfmapi_client_gen.models`,
and hand-written helpers for chunked upload, SSE event streaming,
binary point parsing, and job waiting.

### Plugin install and routing

Operator plugin routes are generated like the rest of the REST API. Use
dry-run requests for planning and inspect only redacted provisioning output:

```python
from sfmapi_client_gen import Client
from sfmapi_client_gen.api.admin.install_plugin_v1_admin_plugins_plugin_id_install_post import sync
from sfmapi_client_gen.models.plugin_install_request import PluginInstallRequest
from sfmapi_client_gen.models.plugin_install_request_method import PluginInstallRequestMethod

client = Client(base_url="http://localhost:8080")
plan = sync(
    "local_test",
    client=client,
    body=PluginInstallRequest(
        method=PluginInstallRequestMethod.UV,
        github_url="https://github.com/SFMAPI/sfmapi_custom.git",
        ref="v0.1.0",
        package_name="sfmapi-custom",
        dry_run=True,
        provision_runtime=True,
    ),
)
print(plan.provisioning.env_keys if plan.provisioning else [])
```

Container-service installs record an already-running service and validate its
health/version protocol before enabling the provider:

```python
from sfmapi_client_gen.api.admin.install_plugin_v1_admin_plugins_plugin_id_install_post import sync
from sfmapi_client_gen.models.plugin_install_request import PluginInstallRequest
from sfmapi_client_gen.models.plugin_install_request_method import PluginInstallRequestMethod

result = sync(
    "instantsfm",
    client=client,
    body=PluginInstallRequest(
        method=PluginInstallRequestMethod.CONTAINER_SERVICE,
        dry_run=False,
        allow_unsafe_execution=True,
        request_id="550e8400-e29b-41d4-a716-446655440010",
    ),
)
print(result.provisioning_status)
```

When several providers can run a stage, set fallback priority explicitly:

```python
from sfmapi_client_gen.api.admin.set_provider_priority_v1_admin_routing_provider_priority_post import sync
from sfmapi_client_gen.models.provider_priority_request import ProviderPriorityRequest

routing = sync(
    client=client,
    body=ProviderPriorityRequest(providers=["colmap_pycolmap", "colmap_cli"]),
)
print(routing.provider_priority)
```

Older generated SDKs can still call these routes through their raw HTTP client.
Treat `provisioning`, `provisioning_status`, `redacted_env`, and
`provider_priority` as additive fields until the SDK is regenerated from the
current OpenAPI snapshot.

## TypeScript client

```bash
cd ../sfmapi-sdk/typescript
npm install
npm run build
```

```ts
import { createSfmApiClient } from "@sfmapi/client/generated";

const client = createSfmApiClient({ baseUrl: "http://localhost:8080" });
```

The package is browser and Node 20 oriented. The repository-local build
produces both ESM and CJS outputs plus generated OpenAPI types.

## C++17 client

```cmake
add_subdirectory(third_party/sfmapi-sdk/cpp)
target_link_libraries(your_target PRIVATE sfmapi_cpp)
```

The C++ client provides wire structs, binary parsers, and a pluggable
HTTP client. It intentionally does not bundle a JSON library or HTTP
transport.
