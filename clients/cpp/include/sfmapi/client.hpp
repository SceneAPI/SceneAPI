// sfmapi C++ HTTP client — pluggable-transport.
//
// HTTP and JSON are platform-specific concerns; rather than picking
// one HTTP library and one JSON library, this header:
//
//   1. Defines a `Transport` callback type that takes an HTTP request
//      shape and returns an HTTP response shape. Consumers plug in:
//        - libcurl on the desktop
//        - cpp-httplib for header-only deployments
//        - Emscripten Fetch in WASM browser builds
//        - any custom std::function<> in tests
//
//   2. Provides a `Client` class that builds the right URL + body for
//      every endpoint and invokes the transport. JSON parsing is
//      delegated to a `JsonAdapter` that the consumer also injects
//      (typedef on a function that converts std::string ↔ generic
//      "value" type — see the README for an nlohmann::json sample).
//
// The result: zero hard dependencies, total flexibility, and one
// consistent surface across desktop/WASM/embedded.
//
// Usage (with libcurl + nlohmann::json):
//
//     #include "sfmapi/client.hpp"
//     #include "sfmapi/transport_curl.hpp"   // your wrapper
//
//     sfmapi::Client c{"http://localhost:8080", MakeCurlTransport(),
//                      MakeNlohmannJsonAdapter()};
//     auto caps = c.Capabilities();
//     if (caps.Supports("dense.patch_match_stereo")) {
//         auto job = c.SubmitDense(recon_id);
//     }

#ifndef SFMAPI_CLIENT_HPP_
#define SFMAPI_CLIENT_HPP_

#include <chrono>
#include <cstdint>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "sfmapi/json.hpp"
#include "sfmapi/sfmapi.hpp"
#include "sfmapi/specs.hpp"
#include "sfmapi/sse.hpp"

namespace sfmapi {

// ====================================================================
//  Transport contract
// ====================================================================

struct HttpRequest {
  std::string method;       // "GET", "POST", "PATCH", "PUT", "DELETE"
  std::string url;          // fully-qualified
  std::map<std::string, std::string> headers;
  std::vector<std::uint8_t> body;
};

struct HttpResponse {
  int status = 0;
  std::map<std::string, std::string> headers;
  std::vector<std::uint8_t> body;
};

/// User-provided HTTP transport. Errors are signaled by throwing a
/// `TransportError` or returning a 5xx response — callers MUST handle
/// both.
using Transport = std::function<HttpResponse(const HttpRequest&)>;

class TransportError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

class HttpStatusError : public std::runtime_error {
 public:
  HttpStatusError(int status, std::string capability)
      : std::runtime_error("sfmapi HTTP status " + std::to_string(status)),
        status_(status),
        capability_(std::move(capability)) {}

  int status() const noexcept { return status_; }
  const std::string& capability() const noexcept { return capability_; }

 private:
  int status_ = 0;
  std::string capability_;
};

// ====================================================================
//  JSON adapter contract — keep the SDK JSON-library-agnostic.
//
//  `JsonValue` is intentionally untyped: it's whatever your JSON
//  library returns. The SDK only ever shuttles JSON between the
//  transport and the consumer. Decoding into `sfmapi::Camera` etc.
//  is the consumer's job (the structs in `sfmapi.hpp` are designed
//  to make that one-liners).
// ====================================================================

using JsonString = std::string;

/// Encode a JSON-shape (e.g. `{"name": "x"}`) to a string. Returns
/// the body to send.
using JsonEncoder = std::function<JsonString(const void* user_data)>;

/// The JSON adapter is a pair of encode + decode functions.
struct JsonAdapter {
  /// Encode a typed body to a JSON string. Implementations typically
  /// take an nlohmann::json / RapidJSON / picojson value via a
  /// type-erased pointer; see the README for ready-made adapters.
  std::function<JsonString(const void* body)> encode;
  /// Decode a JSON string to user-typed value.
  std::function<void(const JsonString& body, void* out)> decode;
};

// ====================================================================
//  Client
// ====================================================================

struct ClientOptions {
  std::string base_url;       // e.g. "http://localhost:8080"
  std::string api_key;        // optional bearer token
  Transport transport;        // required
  JsonAdapter json;           // optional; only methods returning typed
                              // values need it
  std::map<std::string, std::string> default_headers;
};

class Client {
 public:
  explicit Client(ClientOptions opts) : opts_(std::move(opts)) {
    if (!opts_.transport) {
      throw std::invalid_argument("sfmapi::Client requires a Transport");
    }
    while (!opts_.base_url.empty() && opts_.base_url.back() == '/') {
      opts_.base_url.pop_back();
    }
  }

  // --- meta -------------------------------------------------------

  HttpResponse Healthz() const { return Get("/healthz"); }
  HttpResponse Version() const { return Get("/version"); }
  HttpResponse Capabilities() const { return Get("/v1/capabilities"); }

  // --- projects ---------------------------------------------------

  HttpResponse CreateProject(const std::string& name,
                             const std::string& description = "") const {
    return Post("/v1/projects",
                "{\"name\":\"" + JsonEscape(name) + "\"," +
                    "\"description\":" +
                    (description.empty() ? "null"
                                         : "\"" + JsonEscape(description) + "\"") +
                    "}");
  }

  HttpResponse GetProject(const std::string& project_id) const {
    return Get("/v1/projects/" + project_id);
  }

  HttpResponse ListProjects() const { return Get("/v1/projects"); }

  HttpResponse DeleteProject(const std::string& project_id) const {
    return Delete("/v1/projects/" + project_id);
  }

  // --- datasets ---------------------------------------------------

  /// `body` is a pre-encoded JSON object (use the JSON adapter to
  /// build one). The C++ SDK keeps body construction explicit so
  /// you can reuse whatever JSON library your project already has.
  HttpResponse CreateDataset(const std::string& project_id,
                             const std::string& json_body) const {
    return Post("/v1/projects/" + project_id + "/datasets", json_body);
  }

  HttpResponse GetDataset(const std::string& project_id,
                          const std::string& dataset_id) const {
    return Get("/v1/projects/" + project_id + "/datasets/" + dataset_id);
  }

  HttpResponse ListDatasets(const std::string& project_id) const {
    return Get("/v1/projects/" + project_id + "/datasets");
  }

  HttpResponse DeleteDataset(const std::string& project_id,
                             const std::string& dataset_id) const {
    return Delete("/v1/projects/" + project_id + "/datasets/" + dataset_id);
  }

  // --- images / uploads -------------------------------------------

  HttpResponse AddImage(const std::string& dataset_id,
                        const std::string& json_body) const {
    return Post("/v1/datasets/" + dataset_id + "/images", json_body);
  }

  HttpResponse ListImages(const std::string& dataset_id) const {
    return Get("/v1/datasets/" + dataset_id + "/images");
  }

  HttpResponse InitUpload(const std::string& json_body) const {
    return Post("/v1/uploads", json_body);
  }

  HttpResponse PatchChunk(const std::string& upload_id,
                          std::vector<std::uint8_t> data,
                          std::int64_t offset) const {
    HttpRequest req{
        "PATCH",
        opts_.base_url + "/v1/uploads/" + upload_id,
        {{"Content-Range",
          "bytes " + std::to_string(offset) + "-" +
              std::to_string(offset + static_cast<std::int64_t>(data.size()) - 1) +
              "/" + std::to_string(offset + data.size())}},
        std::move(data),
    };
    AddAuth(req);
    return Send(req);
  }

  HttpResponse FinalizeUpload(const std::string& upload_id) const {
    return Post("/v1/uploads/" + upload_id + ":finalize", "{}");
  }

  /// Convenience: chunked upload of an in-memory blob. Initializes
  /// an upload, streams the bytes in ``chunk_size`` increments, and
  /// finalizes. Returns the finalize response (which carries the
  /// content-addressed ``blob_sha`` in its JSON body).
  HttpResponse UploadBytes(
      const std::vector<std::uint8_t>& data,
      std::size_t chunk_size = 1 * 1024 * 1024,
      const std::string& content_type = "") const {
    std::string init_body = "{\"expected_size\":" + std::to_string(data.size());
    if (!content_type.empty()) {
      init_body += ",\"content_type\":\"" + JsonEscape(content_type) + "\"";
    }
    init_body += "}";
    auto init_resp = RaiseOnError(InitUpload(init_body));
    std::string body(init_resp.body.begin(), init_resp.body.end());
    auto pos = body.find("\"upload_id\":\"");
    if (pos == std::string::npos) {
      throw std::runtime_error("UploadBytes: server response has no upload_id");
    }
    pos += std::string("\"upload_id\":\"").size();
    auto end = body.find('"', pos);
    if (end == std::string::npos) {
      throw std::runtime_error("UploadBytes: malformed upload_id");
    }
    std::string upload_id = body.substr(pos, end - pos);
    std::int64_t offset = 0;
    while (offset < static_cast<std::int64_t>(data.size())) {
      auto take = std::min(
          chunk_size, data.size() - static_cast<std::size_t>(offset));
      std::vector<std::uint8_t> chunk(
          data.begin() + offset, data.begin() + offset + take);
      RaiseOnError(PatchChunk(upload_id, std::move(chunk), offset));
      offset += static_cast<std::int64_t>(take);
    }
    return RaiseOnError(FinalizeUpload(upload_id));
  }

  // --- SfM stages -------------------------------------------------

  HttpResponse SubmitFeatures(const std::string& dataset_id,
                              const std::string& json_body = "{}") const {
    return Post("/v1/datasets/" + dataset_id + "/features", json_body);
  }

  HttpResponse SubmitMatches(const std::string& dataset_id,
                             const std::string& json_body = "{}") const {
    return Post("/v1/datasets/" + dataset_id + "/matches", json_body);
  }

  HttpResponse SubmitVerify(const std::string& dataset_id,
                            const std::string& json_body = "{}") const {
    return Post("/v1/datasets/" + dataset_id + "/verify", json_body);
  }

  HttpResponse RunPipeline(const std::string& project_id,
                           const std::string& kind,
                           const std::string& json_body) const {
    return Post("/v1/projects/" + project_id + "/pipelines/" + kind, json_body);
  }

  // --- jobs -------------------------------------------------------

  HttpResponse GetJob(const std::string& job_id) const {
    return Get("/v1/jobs/" + job_id);
  }

  HttpResponse ListJobArtifacts(
      const std::string& job_id,
      const std::string& kind = "",
      const std::string& task_id = "",
      const std::string& name = "",
      const std::string& page_token = "",
      int page_size = 0) const {
    std::string path = "/v1/jobs/" + job_id + "/artifacts";
    bool first = true;
    AppendQueryParam(path, first, "kind", kind);
    AppendQueryParam(path, first, "task_id", task_id);
    AppendQueryParam(path, first, "name", name);
    AppendQueryParam(path, first, "page_token", page_token);
    if (page_size > 0) {
      AppendQueryParam(path, first, "page_size", std::to_string(page_size));
    }
    return Get(path);
  }

  /// Buffered SSE stream for ``GET /v1/jobs/{id}/events``. Returns
  /// the full response body as ``text/event-stream``; pass to
  /// :func:`ParseSseEvents` (from ``sfmapi/sse.hpp``) to decode.
  /// True streaming requires a streaming-capable Transport — drive
  /// the SSE parser yourself with libcurl's ``CURLOPT_WRITEFUNCTION``
  /// or equivalent in that case.
  HttpResponse GetJobEvents(
      const std::string& job_id,
      std::int64_t last_event_id = -1) const {
    HttpRequest req;
    req.method = "GET";
    req.url = opts_.base_url + "/v1/jobs/" + job_id + "/events";
    req.headers["Accept"] = "text/event-stream";
    if (last_event_id >= 0) {
      req.headers["Last-Event-ID"] = std::to_string(last_event_id);
    }
    if (!opts_.api_key.empty()) {
      req.headers["Authorization"] = "Bearer " + opts_.api_key;
    }
    for (const auto& kv : opts_.default_headers) {
      req.headers[kv.first] = kv.second;
    }
    return opts_.transport(std::move(req));
  }

  /// Convenience: fetch + decode the buffered SSE stream in one
  /// call. Mirrors the Python ``stream_events()`` and TS
  /// ``streamEvents()`` ergonomics in the Client OO surface.
  /// Throws a typed ``HttpStatusError`` on any non-2xx open.
  std::vector<SseEvent> StreamEvents(
      const std::string& job_id,
      std::int64_t last_event_id = -1) const {
    auto resp = RaiseOnError(GetJobEvents(job_id, last_event_id));
    return ParseSseEvents(std::string(resp.body.begin(), resp.body.end()));
  }

  HttpResponse CancelJob(const std::string& job_id, bool force = false) const {
    return Post(std::string("/v1/jobs/") + job_id + ":cancel" +
                    (force ? "?force=true" : ""),
                "{}");
  }

  HttpResponse ResumeJob(const std::string& job_id) const {
    return Post("/v1/jobs/" + job_id + ":resume", "{}");
  }

  // --- reconstructions / snapshots --------------------------------

  HttpResponse GetReconstruction(const std::string& recon_id) const {
    return Get("/v1/reconstructions/" + recon_id);
  }

  HttpResponse ListReconstructionArtifacts(
      const std::string& recon_id,
      const std::string& kind = "",
      const std::string& task_id = "",
      const std::string& name = "",
      const std::string& page_token = "",
      int page_size = 0) const {
    std::string path = "/v1/reconstructions/" + recon_id + "/artifacts";
    bool first = true;
    AppendQueryParam(path, first, "kind", kind);
    AppendQueryParam(path, first, "task_id", task_id);
    AppendQueryParam(path, first, "name", name);
    AppendQueryParam(path, first, "page_token", page_token);
    if (page_size > 0) {
      AppendQueryParam(path, first, "page_size", std::to_string(page_size));
    }
    return Get(path);
  }

  HttpResponse ListSubmodels(const std::string& recon_id) const {
    return Get("/v1/reconstructions/" + recon_id + "/submodels");
  }

  HttpResponse ListSnapshots(const std::string& recon_id) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots");
  }

  HttpResponse ReadSnapshotFile(const std::string& recon_id, int seq,
                                const std::string& name) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/" + name);
  }

  HttpResponse ReadTwoViewGeometries(const std::string& recon_id) const {
    return Get("/v1/reconstructions/" + recon_id + "/two_view_geometries.json");
  }

  HttpResponse ReadCorrespondenceGraph(const std::string& recon_id) const {
    return Get("/v1/reconstructions/" + recon_id + "/correspondence_graph.json");
  }

  HttpResponse ReadDenseIndex(const std::string& recon_id, int seq) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/dense/index.json");
  }

  HttpResponse ReadDenseFused(const std::string& recon_id, int seq) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/dense/fused.bin");
  }

  HttpResponse ReadDepthMap(const std::string& recon_id, int seq,
                            const std::string& image_name) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/dense/depth_maps/" + image_name + ".bin");
  }

  HttpResponse ReadNormalMap(const std::string& recon_id, int seq,
                             const std::string& image_name) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/dense/normal_maps/" + image_name + ".bin");
  }

  HttpResponse ReadMeshManifest(const std::string& recon_id, int seq) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/mesh.json");
  }

  HttpResponse ReadMeshPly(const std::string& recon_id, int seq) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/mesh.ply");
  }

  // --- localize / georegister / cubemap / dense / mesh / merge -----

  HttpResponse SubmitLocalize(const std::string& recon_id,
                              const std::string& json_body) const {
    return Post("/v1/reconstructions/" + recon_id + "/localize", json_body);
  }

  HttpResponse SubmitGeoregister(const std::string& recon_id,
                                 const std::string& json_body) const {
    return Post("/v1/reconstructions/" + recon_id + "/georegister", json_body);
  }

  HttpResponse SubmitToCubemap(const std::string& recon_id) const {
    return Post("/v1/reconstructions/" + recon_id + ":to_cubemap", "");
  }

  HttpResponse SubmitRenderCubemap(const std::string& dataset_id,
                                   int face_size = 0) const {
    std::string url = "/v1/datasets/" + dataset_id + ":render_cubemap";
    if (face_size > 0) url += "?face_size=" + std::to_string(face_size);
    return Post(url, "");
  }

  HttpResponse SubmitDense(const std::string& recon_id) const {
    return Post("/v1/reconstructions/" + recon_id + "/dense", "");
  }

  HttpResponse SubmitMesh(const std::string& recon_id,
                          const std::string& json_body = R"({"method":"poisson"})") const {
    return Post("/v1/reconstructions/" + recon_id + "/mesh", json_body);
  }

  HttpResponse SubmitMergeRecons(const std::string& json_body) const {
    return Post("/v1/reconstructions:merge", json_body);
  }

  // --- pose priors -------------------------------------------------

  HttpResponse GetPosePrior(const std::string& image_id) const {
    return Get("/v1/images/" + image_id + "/pose_prior");
  }

  HttpResponse PutPosePrior(const std::string& image_id,
                            const std::string& json_body) const {
    HttpRequest req{
        "PUT",
        opts_.base_url + "/v1/images/" + image_id + "/pose_prior",
        {{"Content-Type", "application/json"}},
        ToBytes(json_body),
    };
    AddAuth(req);
    return Send(req);
  }

  HttpResponse DeletePosePrior(const std::string& image_id) const {
    return Delete("/v1/images/" + image_id + "/pose_prior");
  }

  HttpResponse ListPosePriors(const std::string& dataset_id) const {
    return Get("/v1/datasets/" + dataset_id + "/pose_priors");
  }

  HttpResponse BulkSetPosePriors(const std::string& dataset_id,
                                 const std::string& json_body) const {
    HttpRequest req{
        "PUT",
        opts_.base_url + "/v1/datasets/" + dataset_id + "/pose_priors",
        {{"Content-Type", "application/json"}},
        ToBytes(json_body),
    };
    AddAuth(req);
    return Send(req);
  }

  // --- similarity --------------------------------------------------

  HttpResponse SimilarityNeighbors(const std::string& dataset_id,
                                   const std::string& image_id, int k = 5,
                                   const std::string& strategy = "dhash",
                                   bool include_self = false) const {
    return Get("/v1/datasets/" + dataset_id +
               "/similarity?image_id=" + image_id + "&k=" + std::to_string(k) +
               "&strategy=" + strategy +
               "&include_self=" + (include_self ? "true" : "false"));
  }

  HttpResponse BuildSimilarityIndex(const std::string& dataset_id,
                                    const std::string& strategy = "dhash",
                                    bool force = true) const {
    return Post("/v1/datasets/" + dataset_id +
                    "/similarity:build?strategy=" + strategy +
                    "&force=" + (force ? "true" : "false"),
                "");
  }

  // --- ingest helpers ----------------------------------------------

  HttpResponse SubmitVideoFrames(const std::string& project_id,
                                 const std::string& json_body) const {
    return Post("/v1/projects/" + project_id + "/datasets:from_video", json_body);
  }

  HttpResponse SubmitKaptureImport(const std::string& project_id,
                                   const std::string& json_body) const {
    return Post("/v1/projects/" + project_id + "/datasets:import_kapture",
                json_body);
  }

  // --- meta (extended) -------------------------------------------

  HttpResponse Readyz() const { return Get("/readyz"); }
  HttpResponse Spec() const { return Get("/spec"); }
  HttpResponse OpenApi() const { return Get("/openapi.json"); }
  HttpResponse Metrics() const { return Get("/metrics"); }

  // --- stage artifacts -------------------------------------------

  HttpResponse ListArtifactKinds(
      const std::string& page_token = "",
      int page_size = 0) const {
    std::string path = "/v1/artifacts/kinds";
    bool first = true;
    AppendQueryParam(path, first, "page_token", page_token);
    if (page_size > 0) {
      AppendQueryParam(path, first, "page_size", std::to_string(page_size));
    }
    return Get(path);
  }

  HttpResponse GetArtifact(const std::string& artifact_id) const {
    return Get("/v1/artifacts/" + artifact_id);
  }

  HttpResponse ReadArtifactContent(const std::string& artifact_id,
                                   bool download = false) const {
    return Get("/v1/artifacts/" + artifact_id + "/content" +
               (download ? "?download=true" : ""));
  }

  // --- projects (extended) ---------------------------------------

  HttpResponse PatchProject(const std::string& project_id,
                            const std::string& json_body) const {
    HttpRequest req{
        "PATCH", opts_.base_url + "/v1/projects/" + project_id,
        {{"Content-Type", "application/json"}}, ToBytes(json_body)};
    AddAuth(req);
    return Send(req);
  }

  // --- datasets (extended) ---------------------------------------

  HttpResponse PatchDataset(const std::string& project_id,
                            const std::string& dataset_id,
                            const std::string& json_body) const {
    HttpRequest req{
        "PATCH",
        opts_.base_url + "/v1/projects/" + project_id + "/datasets/" + dataset_id,
        {{"Content-Type", "application/json"}}, ToBytes(json_body)};
    AddAuth(req);
    return Send(req);
  }

  // --- images (extended) -----------------------------------------

  HttpResponse BatchCreateImages(const std::string& dataset_id,
                                 const std::string& json_body) const {
    return Post("/v1/datasets/" + dataset_id + "/images:batchCreate",
                json_body);
  }

  HttpResponse DeleteImage(const std::string& dataset_id,
                           const std::string& name) const {
    return Delete("/v1/datasets/" + dataset_id + "/images/" + name);
  }

  HttpResponse GetImage(const std::string& image_id) const {
    return Get("/v1/images/" + image_id);
  }

  HttpResponse GetImageBytes(const std::string& image_id,
                             bool download = false) const {
    return Get(std::string("/v1/images/") + image_id + "/bytes" +
               (download ? "?download=true" : ""));
  }

  HttpResponse GetImageThumbnail(const std::string& image_id,
                                 int size = 0) const {
    std::string url = "/v1/images/" + image_id + "/thumbnail";
    if (size > 0) url += "?size=" + std::to_string(size);
    return Get(url);
  }

  HttpResponse GetImageExif(const std::string& image_id) const {
    return Get("/v1/images/" + image_id + "/exif");
  }

  // --- uploads (extended) ----------------------------------------

  HttpResponse GetUpload(const std::string& upload_id) const {
    return Get("/v1/uploads/" + upload_id);
  }

  // --- reconstructions / submodels (extended) --------------------

  HttpResponse GetSubmodel(const std::string& submodel_id) const {
    return Get("/v1/submodels/" + submodel_id);
  }

  // --- snapshot inspection (observations / visibility / tiles) ---

  HttpResponse ReadImageObservations(const std::string& recon_id, int seq,
                                     const std::string& image_id) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/images/" + image_id + "/observations");
  }

  HttpResponse ReadPointVisibility(const std::string& recon_id, int seq,
                                   const std::string& point3d_id) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/points/" + point3d_id + "/visibility");
  }

  HttpResponse ReadTilesIndex(const std::string& recon_id, int seq,
                              int max_level = 0) const {
    std::string url = "/v1/reconstructions/" + recon_id + "/snapshots/" +
                      std::to_string(seq) + "/tiles/index.json";
    if (max_level > 0) url += "?max_level=" + std::to_string(max_level);
    return Get(url);
  }

  HttpResponse ReadTile(const std::string& recon_id, int seq, int level,
                        int x, int y, int z) const {
    return Get("/v1/reconstructions/" + recon_id + "/snapshots/" +
               std::to_string(seq) + "/tiles/" + std::to_string(level) + "/" +
               std::to_string(x) + "/" + std::to_string(y) + "/" +
               std::to_string(z) + ".bin");
  }

  // --- admin: api keys -------------------------------------------

  HttpResponse ListApiKeys() const { return Get("/v1/admin/api-keys"); }

  HttpResponse CreateApiKey(const std::string& json_body = "{}") const {
    return Post("/v1/admin/api-keys", json_body);
  }

  HttpResponse DeleteApiKey(const std::string& api_key_id) const {
    return Delete("/v1/admin/api-keys/" + api_key_id);
  }

  // ================================================================
  //  Convenience helpers (mirror Python/TS extras).
  // ================================================================

  /// Read a file from disk and call ``UploadBytes`` on its contents.
  HttpResponse UploadFile(
      const std::string& path,
      std::size_t chunk_size = 1 * 1024 * 1024,
      const std::string& content_type = "") const {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("UploadFile: cannot open " + path);
    std::stringstream ss;
    ss << f.rdbuf();
    std::string s = ss.str();
    std::vector<std::uint8_t> bytes(s.begin(), s.end());
    return UploadBytes(bytes, chunk_size, content_type);
  }

  /// New-style match submission — separate `pairs` and `matcher`
  /// shapes (capabilities ``pairs.{strategy}`` + ``matchers.{type}``).
  HttpResponse SubmitMatchesSplit(const std::string& dataset_id,
                                  const PairsSpec& pairs,
                                  const MatcherSpec& matcher) const {
    Json::Object body{
        {"pairs", pairs.ToJson()},
        {"matcher", matcher.ToJson()},
    };
    return SubmitMatches(dataset_id, Json(std::move(body)).Dump());
  }

  /// Poll a localize job and decode the task output as a
  /// :class:`LocalizationResult`. Throws if the job has no completed
  /// localize task.
  LocalizationResult GetLocalizationResult(const std::string& job_id) const {
    auto resp = RaiseOnError(GetJob(job_id));
    auto body = Json::Parse(std::string(resp.body.begin(), resp.body.end()));
    if (!body.contains("tasks")) {
      throw std::runtime_error("GetLocalizationResult: no tasks in job " + job_id);
    }
    for (const auto& t : body["tasks"].as_array()) {
      if (t.contains("kind") && t["kind"].as_string() == "localize" &&
          t.contains("outputs_ref") && !t["outputs_ref"].is_null()) {
        return LocalizationResultFromJson(t["outputs_ref"]);
      }
    }
    throw std::runtime_error("job " + job_id + " has no completed localize task");
  }

  /// Decode a buffered SSE response body (e.g. from a manual
  /// libcurl streaming download) into discrete events.
  static std::vector<SseEvent> ParseEventsBuffer(const std::string& body) {
    return ParseSseEvents(body);
  }

  /// Decode the response of ``Capabilities()`` into a typed
  /// :class:`Capabilities` struct. Missing ``schema_version`` defaults
  /// to 1 for back-compat with pre-versioned servers.
  static sfmapi::Capabilities ParseCapabilities(const HttpResponse& resp) {
    auto body = Json::Parse(std::string(resp.body.begin(), resp.body.end()));
    sfmapi::Capabilities out;
    if (body.contains("schema_version")) {
      out.schema_version = static_cast<int>(body["schema_version"].as_number());
    }
    if (body.contains("backend")) {
      const auto& b = body["backend"];
      if (b.contains("name")) out.backend.name = b["name"].as_string();
      if (b.contains("version")) out.backend.version = b["version"].as_string();
      if (b.contains("vendor")) out.backend.vendor = b["vendor"].as_string();
    }
    if (body.contains("features")) {
      for (const auto& kv : body["features"].as_object()) {
        out.features[kv.first] = kv.second.is_bool() ? kv.second.as_bool() : false;
      }
    }
    return out;
  }

  // ----- Job rollup + wait helpers ---------------------------------
  //  Mirror the Python `wait_for_job` and TS `waitForJob` ergonomics.
  //  C++ has no built-in sleep+backoff machinery so we keep the
  //  contract minimal: poll ``GetJob`` at a fixed interval until the
  //  status is one of the terminal values, return the parsed
  //  ``JobDetail``, throw a typed ``SfmApiError`` on any non-2xx.

  /// Terminal Job statuses. ``succeeded`` / ``failed`` /
  /// ``cancelled`` / ``cancelled_dirty`` come from the server's Job
  /// finite-state machine — see ``app/workers/dispatcher.py::_maybe_finalize_job``.
  static bool IsTerminalJobStatus(const std::string& s) noexcept {
    return s == "succeeded" || s == "failed" || s == "cancelled" ||
           s == "cancelled_dirty";
  }

  /// Decode a JobDetail body into a typed ``JobDetail`` POD struct.
  static sfmapi::JobDetail ParseJobDetail(const HttpResponse& resp) {
    auto j = Json::Parse(std::string(resp.body.begin(), resp.body.end()));
    return JobDetailFromJson(j);
  }

  /// Block until ``job_id`` reaches a terminal status and return the
  /// final ``JobDetail``.
  ///
  /// ``poll_interval_ms`` controls cadence. ``timeout_ms`` caps the
  /// total wait — throws ``std::runtime_error`` on timeout. Any
  /// non-2xx job poll throws via ``RaiseOnError``.
  ///
  /// Sleep is delegated to the supplied ``sleep_fn`` so consumers
  /// can plug in their own scheduler (single-thread reactor, fiber
  /// pool, etc.) instead of ``std::this_thread::sleep_for``.
  sfmapi::JobDetail WaitForJob(
      const std::string& job_id,
      int poll_interval_ms = 250,
      int timeout_ms = 600 * 1000,
      std::function<void(int)> sleep_fn = nullptr) const {
    auto sleeper = sleep_fn ? sleep_fn : [](int ms) {
      std::this_thread::sleep_for(std::chrono::milliseconds(ms));
    };
    auto start = std::chrono::steady_clock::now();
    while (true) {
      auto resp = RaiseOnError(GetJob(job_id));
      auto detail = ParseJobDetail(resp);
      if (IsTerminalJobStatus(detail.status)) {
        return detail;
      }
      auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                         std::chrono::steady_clock::now() - start)
                         .count();
      if (elapsed >= timeout_ms) {
        throw std::runtime_error(
            "WaitForJob: " + job_id + " still in status='" + detail.status +
            "' after " + std::to_string(timeout_ms) + "ms");
      }
      sleeper(poll_interval_ms);
    }
  }

  /// Submit a job, then stream SSE events live via ``GetJobEvents``
  /// — invoking ``on_event`` for each, until the server closes the
  /// stream — and finally return the terminal :class:`JobDetail`
  /// via :meth:`WaitForJob`.
  ///
  /// C++17 has no generator syntax, so the streaming side is a
  /// callback. ``GetJobEvents`` returns the buffered SSE body as a
  /// single ``HttpResponse``; consumers wanting true streaming
  /// should drive the SSE parser themselves with their HTTP library
  /// of choice (the :func:`ParseSseEvents` helper from
  /// ``sfmapi/sse.hpp`` parses any buffered body).
  sfmapi::JobDetail SubmitAndStream(
      const std::function<HttpResponse()>& submit_fn,
      const std::function<void(const SseEvent&)>& on_event,
      int poll_interval_ms = 250,
      int timeout_ms = 600 * 1000,
      std::function<void(int)> sleep_fn = nullptr) const {
    auto resp = RaiseOnError(submit_fn());
    auto submit = ParseJobSubmitResponse(resp);
    if (submit.job_id.empty()) {
      throw std::runtime_error(
          "SubmitAndStream: submit_fn response carried no job_id");
    }
    // Buffer the SSE body once; tolerate non-200 (server may not
    // emit a stream for very fast jobs that finish before the
    // request lands).
    auto stream_resp = GetJobEvents(submit.job_id);
    if (stream_resp.status == 200 && on_event) {
      std::string body(stream_resp.body.begin(), stream_resp.body.end());
      for (const auto& ev : ParseSseEvents(body)) {
        on_event(ev);
      }
    }
    return WaitForJob(submit.job_id, poll_interval_ms, timeout_ms,
                      std::move(sleep_fn));
  }

  /// Submit a job via ``submit_fn`` then block on :meth:`WaitForJob`
  /// for the returned ``job_id``. The most-used end-to-end consumer
  /// flow: "do the thing, give me the final JobDetail".
  ///
  /// ``submit_fn`` is any callable that returns an
  /// :class:`HttpResponse`; it's typically a closure that calls one
  /// of the ``Submit*`` methods (e.g.
  /// ``[&]() { return SubmitFeatures(...); }``).
  sfmapi::JobDetail SubmitAndWait(
      const std::function<HttpResponse()>& submit_fn,
      int poll_interval_ms = 250,
      int timeout_ms = 600 * 1000,
      std::function<void(int)> sleep_fn = nullptr) const {
    auto resp = RaiseOnError(submit_fn());
    auto submit = ParseJobSubmitResponse(resp);
    if (submit.job_id.empty()) {
      throw std::runtime_error(
          "SubmitAndWait: submit_fn response carried no job_id");
    }
    return WaitForJob(submit.job_id, poll_interval_ms, timeout_ms,
                      std::move(sleep_fn));
  }

  /// Decode the response of any ``Submit*()`` into a typed
  /// :class:`JobSubmitResponse`.
  static sfmapi::JobSubmitResponse ParseJobSubmitResponse(
      const HttpResponse& resp) {
    auto body = Json::Parse(std::string(resp.body.begin(), resp.body.end()));
    sfmapi::JobSubmitResponse out;
    if (body.contains("job_id")) out.job_id = body["job_id"].as_string();
    if (body.contains("recon_id") && !body["recon_id"].is_null()) {
      out.recon_id = body["recon_id"].as_string();
    }
    if (body.contains("task_ids")) {
      for (const auto& t : body["task_ids"].as_array()) {
        out.task_ids.push_back(t.as_string());
      }
    }
    return out;
  }

  static sfmapi::ArtifactKind ArtifactKindFromJson(const Json& j) {
    sfmapi::ArtifactKind out;
    if (j.contains("kind")) out.kind = j["kind"].as_string();
    if (j.contains("title")) out.title = j["title"].as_string();
    if (j.contains("description")) out.description = j["description"].as_string();
    if (j.contains("durable")) out.durable = j["durable"].as_bool();
    return out;
  }

  static sfmapi::ArtifactKind ParseArtifactKind(const HttpResponse& resp) {
    return ArtifactKindFromJson(
        Json::Parse(std::string(resp.body.begin(), resp.body.end())));
  }

  static Page<sfmapi::ArtifactKind> ParseArtifactKindPage(
      const HttpResponse& resp) {
    return ParsePage<sfmapi::ArtifactKind>(
        resp, [](const Json& item) { return ArtifactKindFromJson(item); });
  }

  static sfmapi::StageArtifact StageArtifactFromJson(const Json& j) {
    sfmapi::StageArtifact out;
    if (j.contains("artifact_id")) out.artifact_id = j["artifact_id"].as_string();
    if (j.contains("job_id")) out.job_id = j["job_id"].as_string();
    if (j.contains("task_id")) out.task_id = j["task_id"].as_string();
    if (j.contains("recon_id") && !j["recon_id"].is_null()) {
      out.recon_id = j["recon_id"].as_string();
    }
    if (j.contains("dataset_id") && !j["dataset_id"].is_null()) {
      out.dataset_id = j["dataset_id"].as_string();
    }
    if (j.contains("kind")) out.kind = j["kind"].as_string();
    if (j.contains("name") && !j["name"].is_null()) {
      out.name = j["name"].as_string();
    }
    if (j.contains("uri") && !j["uri"].is_null()) {
      out.uri = j["uri"].as_string();
    }
    if (j.contains("media_type") && !j["media_type"].is_null()) {
      out.media_type = j["media_type"].as_string();
    }
    if (j.contains("summary") && !j["summary"].is_null()) {
      out.summary_json = j["summary"].Dump();
    }
    if (j.contains("metadata") && !j["metadata"].is_null()) {
      out.metadata_json = j["metadata"].Dump();
    }
    if (j.contains("_links") && !j["_links"].is_null()) {
      out.links_json = j["_links"].Dump();
    }
    if (j.contains("created_at")) out.created_at = j["created_at"].as_string();
    return out;
  }

  static sfmapi::StageArtifact ParseStageArtifact(const HttpResponse& resp) {
    return StageArtifactFromJson(
        Json::Parse(std::string(resp.body.begin(), resp.body.end())));
  }

  static Page<sfmapi::StageArtifact> ParseStageArtifactPage(
      const HttpResponse& resp) {
    return ParsePage<sfmapi::StageArtifact>(
        resp, [](const Json& item) { return StageArtifactFromJson(item); });
  }

  // ----- generic JSON-body decoders for resource shapes ------------
  //  These are the C++ analogues of Python Pydantic validation +
  //  TypeScript interface assignment: read a JSON value and populate
  //  a typed POD struct. Fields absent from the body keep their POD
  //  defaults so partial responses don't crash.

  static sfmapi::Project ProjectFromJson(const Json& j) {
    sfmapi::Project out;
    if (j.contains("project_id")) out.project_id = j["project_id"].as_string();
    if (j.contains("tenant_id")) out.tenant_id = j["tenant_id"].as_string();
    if (j.contains("name")) out.name = j["name"].as_string();
    if (j.contains("description") && !j["description"].is_null()) {
      out.description = j["description"].as_string();
    }
    if (j.contains("created_at")) out.created_at = j["created_at"].as_string();
    return out;
  }

  static sfmapi::Project ParseProject(const HttpResponse& resp) {
    return ProjectFromJson(
        Json::Parse(std::string(resp.body.begin(), resp.body.end())));
  }

  static sfmapi::Dataset DatasetFromJson(const Json& j) {
    sfmapi::Dataset out;
    if (j.contains("dataset_id")) out.dataset_id = j["dataset_id"].as_string();
    if (j.contains("tenant_id")) out.tenant_id = j["tenant_id"].as_string();
    if (j.contains("project_id")) out.project_id = j["project_id"].as_string();
    if (j.contains("source_id") && !j["source_id"].is_null()) {
      out.source_id = j["source_id"].as_string();
    }
    if (j.contains("name")) out.name = j["name"].as_string();
    if (j.contains("camera_model")) out.camera_model = j["camera_model"].as_string();
    if (j.contains("intrinsics_mode")) {
      out.intrinsics_mode = j["intrinsics_mode"].as_string();
    }
    if (j.contains("is_spherical")) out.is_spherical = j["is_spherical"].as_bool();
    if (j.contains("respect_exif_orientation")) {
      out.respect_exif_orientation = j["respect_exif_orientation"].as_bool();
    }
    if (j.contains("active_maskset_id") && !j["active_maskset_id"].is_null()) {
      out.active_maskset_id = j["active_maskset_id"].as_string();
    }
    if (j.contains("manifest_hash") && !j["manifest_hash"].is_null()) {
      out.manifest_hash = j["manifest_hash"].as_string();
    }
    if (j.contains("created_at")) out.created_at = j["created_at"].as_string();
    return out;
  }

  static sfmapi::Dataset ParseDataset(const HttpResponse& resp) {
    return DatasetFromJson(
        Json::Parse(std::string(resp.body.begin(), resp.body.end())));
  }

  static sfmapi::TaskRow TaskRowFromJson(const Json& j) {
    sfmapi::TaskRow out;
    if (j.contains("task_id")) out.task_id = j["task_id"].as_string();
    if (j.contains("job_id")) out.job_id = j["job_id"].as_string();
    if (j.contains("kind")) out.kind = j["kind"].as_string();
    if (j.contains("status")) out.status = j["status"].as_string();
    if (j.contains("cache_key")) out.cache_key = j["cache_key"].as_string();
    if (j.contains("inputs_hash")) out.inputs_hash = j["inputs_hash"].as_string();
    if (j.contains("params_hash")) out.params_hash = j["params_hash"].as_string();
    if (j.contains("outputs_ref") && !j["outputs_ref"].is_null()) {
      out.outputs_ref_json = j["outputs_ref"].Dump();
    }
    return out;
  }

  static sfmapi::JobDetail JobDetailFromJson(const Json& j) {
    sfmapi::JobDetail out;
    if (j.contains("job_id")) out.job_id = j["job_id"].as_string();
    if (j.contains("tenant_id")) out.tenant_id = j["tenant_id"].as_string();
    if (j.contains("project_id")) out.project_id = j["project_id"].as_string();
    if (j.contains("recipe")) out.recipe = j["recipe"].as_string();
    if (j.contains("status")) out.status = j["status"].as_string();
    if (j.contains("cancel_requested")) {
      out.cancel_requested = j["cancel_requested"].as_bool();
    }
    if (j.contains("cancel_force")) {
      out.cancel_force = j["cancel_force"].as_bool();
    }
    if (j.contains("created_at")) out.created_at = j["created_at"].as_string();
    if (j.contains("started_at") && !j["started_at"].is_null()) {
      out.started_at = j["started_at"].as_string();
    }
    if (j.contains("finished_at") && !j["finished_at"].is_null()) {
      out.finished_at = j["finished_at"].as_string();
    }
    if (j.contains("error_class") && !j["error_class"].is_null()) {
      out.error_class = j["error_class"].as_string();
    }
    if (j.contains("error_message") && !j["error_message"].is_null()) {
      out.error_message = j["error_message"].as_string();
    }
    if (j.contains("tasks") && j["tasks"].is_array()) {
      for (const auto& t : j["tasks"].as_array()) {
        out.tasks.push_back(TaskRowFromJson(t));
      }
    }
    return out;
  }

  static sfmapi::HealthResponse ParseHealthResponse(const HttpResponse& resp) {
    auto j = Json::Parse(std::string(resp.body.begin(), resp.body.end()));
    sfmapi::HealthResponse out;
    if (j.contains("status")) out.status = j["status"].as_string();
    return out;
  }

  static sfmapi::VersionResponse ParseVersionResponse(const HttpResponse& resp) {
    auto j = Json::Parse(std::string(resp.body.begin(), resp.body.end()));
    sfmapi::VersionResponse out;
    if (j.contains("sfmapi")) out.sfmapi = j["sfmapi"].as_string();
    if (j.contains("backend") && !j["backend"].is_null()) {
      auto& jb = j["backend"];
      sfmapi::BackendVersion bv;
      if (jb.contains("name")) bv.name = jb["name"].as_string();
      if (jb.contains("version")) bv.version = jb["version"].as_string();
      if (jb.contains("vendor") && !jb["vendor"].is_null()) {
        bv.vendor = jb["vendor"].as_string();
      }
      if (jb.contains("runtime_versions")) {
        for (auto& kv : jb["runtime_versions"].as_object()) {
          bv.runtime_versions[kv.first] = kv.second.as_string();
        }
      }
      out.backend = bv;
    }
    return out;
  }

  /// Decode a Page<T> envelope. ``item_decode`` is a function that
  /// converts a single ``Json`` item to T.
  template <typename T>
  static Page<T> ParsePage(
      const HttpResponse& resp,
      const std::function<T(const Json&)>& item_decode) {
    auto j = Json::Parse(std::string(resp.body.begin(), resp.body.end()));
    Page<T> out;
    if (j.contains("items")) {
      for (const auto& item : j["items"].as_array()) {
        out.items.push_back(item_decode(item));
      }
    }
    if (j.contains("next_page_token") && !j["next_page_token"].is_null()) {
      out.next_page_token = j["next_page_token"].as_string();
    }
    if (j.contains("total") && !j["total"].is_null()) {
      out.total = static_cast<std::int64_t>(j["total"].as_number());
    }
    return out;
  }

 private:
  static LocalizationResult LocalizationResultFromJson(const Json& j) {
    LocalizationResult out;
    if (j.contains("success")) out.success = j["success"].as_bool();
    if (j.contains("num_inliers")) {
      out.num_inliers = static_cast<std::int32_t>(j["num_inliers"].as_number());
    }
    if (j.contains("cam_from_world") && !j["cam_from_world"].is_null()) {
      const auto& cfw = j["cam_from_world"];
      Rigid3 r;
      if (cfw.contains("rotation")) {
        const auto& rot = cfw["rotation"];
        r.rotation.w = rot["w"].as_number();
        r.rotation.x = rot["x"].as_number();
        r.rotation.y = rot["y"].as_number();
        r.rotation.z = rot["z"].as_number();
      }
      if (cfw.contains("translation")) {
        const auto& t = cfw["translation"].as_array();
        for (std::size_t i = 0; i < 3 && i < t.size(); ++i) {
          r.translation[i] = t[i].as_number();
        }
      }
      out.cam_from_world = r;
    }
    if (j.contains("inlier_matches")) {
      for (const auto& pair : j["inlier_matches"].as_array()) {
        if (pair.is_array() && pair.as_array().size() >= 2) {
          out.inlier_matches.emplace_back(
              static_cast<std::int32_t>(pair[0].as_number()),
              static_cast<std::int64_t>(pair[1].as_number()));
        }
      }
    }
    return out;
  }

 public:

  // --- helpers / utility ------------------------------------------

  /// Raise on status >= 400. Returns the response unchanged on 2xx/3xx.
  /// Optional helper — most callers will want to call this on every
  /// response to centralize error handling.
  static HttpResponse RaiseOnError(HttpResponse resp) {
    if (resp.status >= 400) {
      // Best-effort: pull `capability` out of a problem+json body so
      // callers can switch on it. We don't depend on a JSON library
      // here — the body is whatever the server returned and the
      // caller can decode it themselves if richer info is needed.
      std::string body(resp.body.begin(), resp.body.end());
      auto pos = body.find("\"capability\":\"");
      std::string cap;
      if (pos != std::string::npos) {
        pos += std::string("\"capability\":\"").size();
        auto end = body.find('"', pos);
        if (end != std::string::npos) cap = body.substr(pos, end - pos);
      }
      throw HttpStatusError(resp.status, std::move(cap));
    }
    return resp;
  }

 private:
  ClientOptions opts_;

  HttpResponse Get(const std::string& path) const {
    HttpRequest req{"GET", opts_.base_url + path, {}, {}};
    AddAuth(req);
    return Send(req);
  }

  HttpResponse Post(const std::string& path, const std::string& body) const {
    HttpRequest req{
        "POST", opts_.base_url + path,
        {{"Content-Type", "application/json"}}, ToBytes(body)};
    AddAuth(req);
    return Send(req);
  }

  HttpResponse Delete(const std::string& path) const {
    HttpRequest req{"DELETE", opts_.base_url + path, {}, {}};
    AddAuth(req);
    return Send(req);
  }

  HttpResponse Send(HttpRequest req) const {
    for (const auto& kv : opts_.default_headers) {
      if (req.headers.find(kv.first) == req.headers.end()) {
        req.headers.insert(kv);
      }
    }
    return opts_.transport(req);
  }

  void AddAuth(HttpRequest& req) const {
    if (!opts_.api_key.empty() &&
        req.headers.find("Authorization") == req.headers.end()) {
      req.headers["Authorization"] = "Bearer " + opts_.api_key;
    }
  }

  static std::vector<std::uint8_t> ToBytes(const std::string& s) {
    return {s.begin(), s.end()};
  }

  static void AppendQueryParam(std::string& path, bool& first,
                               const std::string& name,
                               const std::string& value) {
    if (value.empty()) {
      return;
    }
    path += first ? "?" : "&";
    first = false;
    path += name + "=" + value;
  }

  static std::string JsonEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
      switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default: out += c;
      }
    }
    return out;
  }
};

}  // namespace sfmapi

#endif  // SFMAPI_CLIENT_HPP_
