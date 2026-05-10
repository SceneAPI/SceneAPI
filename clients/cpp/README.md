# sfmapi C++ client (header-only, pluggable transport)

C++17 mirror of the sfmapi wire standard, plus a header-only HTTP
client with a pluggable transport. Suitable for:

- **WebAssembly** consumers (Emscripten) using the Fetch API as the
  transport.
- Native C++ tools using libcurl, cpp-httplib, or boost::beast.
- Custom backends that want to *produce* sfmapi-compliant outputs from
  C++ code.

## Files

```
include/sfmapi/sfmapi.hpp     Wire types + binary parsers.
include/sfmapi/client.hpp     HTTP client with pluggable transport.
test/test_binary.cpp          Smoke test for the binary readers.
test/test_client.cpp          Smoke test for the HTTP client (in-memory
                              mock transport — no network needed).
CMakeLists.txt                Optional: builds both smoke tests.
```

## HTTP client quick start

```cpp
#include "sfmapi/client.hpp"

sfmapi::Client c({
    .base_url = "http://localhost:8080",
    .api_key  = "secret",
    .transport = MakeYourTransport(),  // see "Transports" below
});

auto resp = sfmapi::Client::RaiseOnError(c.Capabilities());
// resp.body is JSON bytes; decode with your JSON library into
// sfmapi::Capabilities, then `.Supports("dense.patch_match_stereo")`.
```

The client surface mirrors the Python and TypeScript SDKs — every
endpoint has a method (`SubmitDense`, `SubmitMesh`, `SubmitLocalize`,
`ReadDenseIndex`, `PutPosePrior`, `SimilarityNeighbors`,
`SubmitVideoFrames`, `ListJobArtifacts`, `GetArtifact`, ...). Errors come back as `HttpStatusError`
exceptions whose `capability()` field carries the canonical name when
the body is a problem+json with a `capability` extra.

## Transports

The `Transport` callback type is `std::function<HttpResponse(const HttpRequest&)>`.
Plug in whatever HTTP library your project already uses:

- **libcurl** (~30 lines, see below)
- **cpp-httplib** (~10 lines, drop-in)
- **boost::beast** (~50 lines, full-featured)
- **Emscripten Fetch** for WASM browser builds
- **In-memory mock** for tests

A libcurl example:

```cpp
#include <curl/curl.h>

sfmapi::Transport MakeCurlTransport() {
  return [](const sfmapi::HttpRequest& req) {
    auto curl = curl_easy_init();
    if (!curl) throw sfmapi::TransportError("curl_easy_init failed");
    sfmapi::HttpResponse out;
    curl_easy_setopt(curl, CURLOPT_URL, req.url.c_str());
    curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, req.method.c_str());
    curl_slist* hdrs = nullptr;
    for (auto& kv : req.headers)
      hdrs = curl_slist_append(hdrs, (kv.first + ": " + kv.second).c_str());
    if (hdrs) curl_easy_setopt(curl, CURLOPT_HTTPHEADER, hdrs);
    if (!req.body.empty()) {
      curl_easy_setopt(curl, CURLOPT_POSTFIELDS, req.body.data());
      curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, req.body.size());
    }
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION,
        +[](char* p, size_t s, size_t n, void* ud) -> size_t {
          auto* o = static_cast<sfmapi::HttpResponse*>(ud);
          o->body.insert(o->body.end(), p, p + s * n);
          return s * n;
        });
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &out);
    auto rc = curl_easy_perform(curl);
    long status = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
    out.status = static_cast<int>(status);
    if (hdrs) curl_slist_free_all(hdrs);
    curl_easy_cleanup(curl);
    if (rc != CURLE_OK) throw sfmapi::TransportError(curl_easy_strerror(rc));
    return out;
  };
}
```

## What's in the header

Every wire type from `app/schemas/api/scene.py`:

- Geometry: `Rotation`, `Rigid3`, `Sim3`, `GpsCoord`, `ImuMeasurement`, `PosePrior`
- Cameras / images: `Camera` (+ `IsSpherical()` helper), `Point2D`, `ImagePose`,
  `TrackElement`, `Track`
- Rigs / frames: `Rig`, `Frame`
- Two-view geometry + correspondences: `TwoViewGeometry`,
  `TwoViewGeometryType`, `CorrespondencePair`
- Pose graph: `PoseGraph`, `PoseGraphEdge`
- Snapshot file wrappers: `CamerasFile`, `ImagesFile`, `RigsFile`,
  `FramesFile`, `TwoViewGeometriesFile`, `CorrespondenceGraphFile`,
  `PoseGraphFile`
- Localization: `LocalizationResult`
- Dense MVS: `DepthMapInfo`, `DenseSummary`, `DenseManifestFile`
- Mesh: `MeshSummary`, `MeshFile`, `MeshMethod`
- Stage artifacts: `ArtifactKind`, `StageArtifact`, plus
  `ArtifactRef` / `ArtifactInputMap` in `specs.hpp` for selecting
  previous stage outputs.
- Capabilities: `BackendInfo`, `Capabilities` (+ `Supports()` helper)

Wire-format binary readers:

- `ParsePointsBinary(data, len)` → `PointsBinary`
  (`application/x-sfm-points-v1`, 44-byte header + 26B records)
- `ParseDepthMap(data, len)` → `DepthMap`
  (`application/x-sfm-depth-v1`, 32-byte header + W·H float32)
- `ParseNormalMap(data, len)` → `NormalMap`
  (`application/x-sfm-normal-v1`, 32-byte header + W·H·3 float32)

All readers throw `sfmapi::WireFormatError` on a bad magic / version /
short body.

## What's NOT in the header

**JSON parsing.** Pick your favorite (`nlohmann::json`, RapidJSON,
simdjson) and map fields to the structs manually. The structs are
designed to be JSON-friendly: every field is a value type with a
default, every container is `std::vector` or `std::map`. A typical
`from_json` reader for `Rotation` is:

```cpp
sfmapi::Rotation FromJson(const json& j) {
  return {j.at("w"), j.at("x"), j.at("y"), j.at("z")};
}
```

**HTTP transport.** Use libcurl, cpp-httplib, or whatever your project
already has. The C++ client is intentionally just types + binary
parsers; networking is decoupled.

**Pycolmap.** This header does not depend on COLMAP or pycolmap. It's
pure standard library.

## Building the smoke test

```sh
cmake -S clients/cpp -B clients/cpp/build
cmake --build clients/cpp/build
ctest --test-dir clients/cpp/build
```

## Use as a CMake subdirectory

```cmake
add_subdirectory(third_party/sfmapi/clients/cpp)
target_link_libraries(your_target PRIVATE sfmapi_cpp)
```

`sfmapi_cpp` is an `INTERFACE` target — it only adds the include
directory + bumps `cxx_std_17` on consumers.

## Use with Emscripten / WASM

```sh
emcmake cmake -S clients/cpp -B build-wasm -DSFMAPI_CPP_TESTS=OFF
emmake cmake --build build-wasm
```

Then include `sfmapi/sfmapi.hpp` from your Emscripten source. The
binary parsers are zero-allocation past `std::vector::resize`, so
they're suitable for browser-side consumption of large point clouds /
depth maps without memory pressure.

## Convention reminder

All quaternions on the wire are **Hamilton (w, x, y, z)** — scalar
first. If you're interoperating with Eigen (which uses (x, y, z, w)),
reorder at the boundary:

```cpp
Eigen::Quaterniond ToEigen(const sfmapi::Rotation& r) {
  return Eigen::Quaterniond(r.w, r.x, r.y, r.z);  // ctor is (w, x, y, z)
}
```
