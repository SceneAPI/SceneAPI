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
