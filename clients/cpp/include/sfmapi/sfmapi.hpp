// sfmapi — single-header C++ data abstractions for the wire standard.
//
// Mirrors the Python schemas in `app/schemas/api/scene.py`,
// `app/schemas/pipeline_spec.py`, `app/schemas/points_binary.py`,
// `app/schemas/depth_map_binary.py`, and `app/core/capabilities.py`.
//
// Designed for browser-side WASM consumers (Emscripten) and native
// C++ tools that fetch sfmapi snapshots, but the header has zero
// external dependencies — it's pure C++17 standard library + the
// Pydantic-equivalent POD structs you'd expect.
//
// Quaternion convention: Hamilton (w, x, y, z), scalar-first — same
// as the wire format. Convert to/from Eigen's (x, y, z, w) at the
// boundary if you need it.
//
// Usage:
//
//     #include "sfmapi/sfmapi.hpp"
//
//     auto pts = sfmapi::ParsePointsBinary(buffer, len);
//     auto depth = sfmapi::ParseDepthMap(buffer, len);
//     auto caps = sfmapi::Capabilities{};   // populate from JSON yourself
//     if (caps.Supports("dense.patch_match_stereo")) { ... }
//
// JSON parsing is intentionally NOT included — pick your favorite
// (nlohmann::json, RapidJSON, simdjson, ...) and map fields manually.
// The structs below are designed to be JSON-friendly: every field
// is a value type with a default, every container is std::vector
// or std::map.
//
// License: same as the sfmapi project.

#ifndef SFMAPI_SFMAPI_HPP_
#define SFMAPI_SFMAPI_HPP_

#include <array>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace sfmapi {

// ====================================================================
//  Geometry
// ====================================================================

/// Hamilton quaternion stored (w, x, y, z) — scalar-first.
struct Rotation {
  double w = 1.0;
  double x = 0.0;
  double y = 0.0;
  double z = 0.0;
};

struct Rigid3 {
  Rotation rotation{};
  std::array<double, 3> translation{0.0, 0.0, 0.0};
};

struct Sim3 {
  Rotation rotation{};
  std::array<double, 3> translation{0.0, 0.0, 0.0};
  double scale = 1.0;
};

struct GpsCoord {
  double lat = 0.0;
  double lng = 0.0;
  std::optional<double> alt;
  std::optional<double> horiz_accuracy_m;
  std::optional<double> vert_accuracy_m;
};

struct ImuMeasurement {
  std::int64_t timestamp_ns = 0;
  std::array<double, 3> gyro{0.0, 0.0, 0.0};
  std::array<double, 3> accel{0.0, 0.0, 0.0};
};

struct PosePrior {
  Rigid3 cam_from_world{};
  /// Row-major 6x6 covariance over (rx, ry, rz, tx, ty, tz). Empty
  /// when no covariance is supplied.
  std::vector<double> covariance;
  std::optional<GpsCoord> gps;
  std::optional<std::int64_t> timestamp_ns;
  std::optional<ImuMeasurement> imu;
};

// ====================================================================
//  Camera + image + tracks
// ====================================================================

/// Wire constant for equirectangular-panorama cameras. `params` MUST
/// be empty when this model is used.
inline constexpr const char* kSphericalCameraModel = "SPHERICAL";

struct Camera {
  std::int32_t camera_id = 0;
  /// COLMAP-style camera-model name. Use kSphericalCameraModel for
  /// equirectangular panoramas.
  std::string model;
  std::int32_t width = 0;
  std::int32_t height = 0;
  std::vector<double> params;
  bool has_prior_focal_length = false;

  bool IsSpherical() const noexcept {
    return model == kSphericalCameraModel;
  }
};

struct Point2D {
  std::array<double, 2> xy{0.0, 0.0};
  /// Empty when the keypoint is not (yet) part of a 3D track.
  std::optional<std::int64_t> point3d_id;
};

struct ImagePose {
  std::int32_t image_id = 0;
  std::string name;
  std::int32_t camera_id = 0;
  Rigid3 cam_from_world{};
  std::vector<Point2D> points2D;
};

struct TrackElement {
  std::int32_t image_id = 0;
  std::int32_t point2d_idx = 0;
};

struct Track {
  std::int64_t point3d_id = 0;
  std::vector<TrackElement> elements;
};

// ====================================================================
//  Rig + frame
// ====================================================================

struct Rig {
  std::int32_t rig_id = 0;
  std::int32_t ref_sensor_id = 0;
  /// Sensor-id (string) → cam_from_rig transform.
  std::map<std::string, Rigid3> sensor_from_rig;
};

struct Frame {
  std::int32_t frame_id = 0;
  std::int32_t rig_id = 0;
  Rigid3 rig_from_world{};
  /// Sensor-id (string) → image_id binding.
  std::map<std::string, std::int32_t> data_ids;
};

// ====================================================================
//  Two-view geometry + correspondences + pose graph
// ====================================================================

enum class TwoViewGeometryType {
  kUndefined,
  kDegenerate,
  kCalibrated,
  kUncalibrated,
  kPlanar,
  kPanoramic,
  kPlanarOrPanoramic,
  kWatermark,
  kMultiple,
};

inline TwoViewGeometryType TwoViewGeometryTypeFromWire(const std::string& s) {
  if (s == "calibrated") return TwoViewGeometryType::kCalibrated;
  if (s == "uncalibrated") return TwoViewGeometryType::kUncalibrated;
  if (s == "planar") return TwoViewGeometryType::kPlanar;
  if (s == "panoramic") return TwoViewGeometryType::kPanoramic;
  if (s == "planar_or_panoramic") return TwoViewGeometryType::kPlanarOrPanoramic;
  if (s == "degenerate") return TwoViewGeometryType::kDegenerate;
  if (s == "watermark") return TwoViewGeometryType::kWatermark;
  if (s == "multiple") return TwoViewGeometryType::kMultiple;
  return TwoViewGeometryType::kUndefined;
}

struct TwoViewGeometry {
  std::int32_t image_id1 = 0;
  std::int32_t image_id2 = 0;
  TwoViewGeometryType type = TwoViewGeometryType::kUndefined;
  std::int32_t num_inliers = 0;
  /// Row-major 3x3; only the matrix matching `type` is populated.
  /// Empty when not present.
  std::array<double, 9> F{};
  bool F_present = false;
  std::array<double, 9> E{};
  bool E_present = false;
  std::array<double, 9> H{};
  bool H_present = false;
  std::vector<std::pair<std::int32_t, std::int32_t>> inlier_matches;
};

struct CorrespondencePair {
  std::int32_t image_id1 = 0;
  std::int32_t image_id2 = 0;
  std::int32_t num_matches = 0;
  /// Raw (pre-verification) `(kp_idx_1, kp_idx_2)` matches.
  std::vector<std::pair<std::int32_t, std::int32_t>> matches;
};

struct PoseGraphEdge {
  std::int32_t image_id1 = 0;
  std::int32_t image_id2 = 0;
  Rigid3 cam2_from_cam1{};
  double weight = 1.0;
};

struct PoseGraph {
  std::vector<ImagePose> nodes;
  std::vector<PoseGraphEdge> edges;
};

// ====================================================================
//  Snapshot file wrappers
// ====================================================================

struct CamerasFile { std::vector<Camera> cameras; };
struct ImagesFile { std::vector<ImagePose> images; };
struct RigsFile { std::vector<Rig> rigs; };
struct FramesFile { std::vector<Frame> frames; };
struct TwoViewGeometriesFile { std::vector<TwoViewGeometry> pairs; };
struct CorrespondenceGraphFile { std::vector<CorrespondencePair> pairs; };
struct PoseGraphFile { PoseGraph pose_graph; };

// ====================================================================
//  Localization + dense + mesh
// ====================================================================

struct LocalizationResult {
  bool success = false;
  std::optional<Rigid3> cam_from_world;
  std::int32_t num_inliers = 0;
  /// `(query_keypoint_idx, point3d_id)` inliers.
  std::vector<std::pair<std::int32_t, std::int64_t>> inlier_matches;
};

struct DepthMapInfo {
  std::int32_t image_id = 0;
  std::string image_name;
  std::int32_t width = 0;
  std::int32_t height = 0;
  double depth_min = 0.0;
  double depth_max = 0.0;
  bool has_normal_map = false;
};

struct DenseSummary {
  std::int32_t num_images = 0;
  std::int32_t num_depth_maps = 0;
  std::int32_t num_normal_maps = 0;
  std::int32_t fused_points = 0;
  std::optional<std::array<double, 3>> bbox_min;
  std::optional<std::array<double, 3>> bbox_max;
};

struct DenseManifestFile {
  DenseSummary summary{};
  std::vector<DepthMapInfo> depth_maps;
};

enum class MeshMethod { kPoisson, kDelaunay };

inline MeshMethod MeshMethodFromWire(const std::string& s) {
  if (s == "delaunay") return MeshMethod::kDelaunay;
  return MeshMethod::kPoisson;  // default + fallback
}

struct MeshSummary {
  MeshMethod method = MeshMethod::kPoisson;
  std::int64_t num_vertices = 0;
  std::int64_t num_faces = 0;
  bool has_vertex_colors = false;
  bool has_vertex_normals = false;
  std::optional<std::array<double, 3>> bbox_min;
  std::optional<std::array<double, 3>> bbox_max;
};

struct MeshFile {
  MeshSummary summary{};
  std::optional<std::string> mesh_url;
};

// ====================================================================
//  Wire-stable resource shapes (mirror of app/schemas/api/{projects,
//  datasets, images, jobs, reconstructions, uploads, common}.py)
//
//  These mirror the JSON the server emits for CRUD reads. Decode JSON
//  with your library of choice; every field is a value type so ctor-
//  style construction works.
// ====================================================================

template <typename T>
struct Page {
  std::vector<T> items;
  std::string next_page_token; // empty when no next page (AIP-158)
  std::int64_t total = -1;     // -1 when not provided
};

struct Project {
  std::string project_id;
  std::string tenant_id;
  std::string name;
  std::string description;
  std::string created_at;     // ISO 8601
};

struct ProjectCreate {
  std::string name;
  std::string description;
};

struct ProjectPatch {
  std::optional<std::string> name;
  std::optional<std::string> description;
};

struct Dataset {
  std::string dataset_id;
  std::string tenant_id;
  std::string project_id;
  std::string source_id;
  std::string name;
  std::string camera_model;
  std::string intrinsics_mode;
  bool is_spherical = false;
  bool respect_exif_orientation = false;
  std::string active_maskset_id;
  std::string manifest_hash;
  std::string created_at;
};

struct DatasetPatch {
  std::optional<std::string> name;
  std::optional<std::string> camera_model;
  std::optional<std::string> intrinsics_mode;
  std::optional<bool> is_spherical;
  std::optional<bool> respect_exif_orientation;
  std::optional<std::string> active_maskset_id;
};

struct ImageRow {
  std::string image_id;
  std::string dataset_id;
  std::string name;
  std::string content_sha;
  std::string source_kind;     // "upload" | "local" | "s3"
  std::string rel_path;
  std::int64_t byte_size = -1;
  std::int32_t width = -1;
  std::int32_t height = -1;
  std::string created_at;
};

struct ImageObservationRow {
  std::int64_t point3d_id = 0;
  double x = 0.0;
  double y = 0.0;
  std::int32_t kp_idx = -1;
  std::optional<double> error;
};

struct PointObservationRow {
  std::int32_t image_id = 0;
  double x = 0.0;
  double y = 0.0;
  std::int32_t kp_idx = -1;
};

struct TilesIndex {
  std::array<double, 3> bbox_min{0, 0, 0};
  std::array<double, 3> bbox_max{0, 0, 0};
  /// Per-level metadata. Schema is implementation-defined; consumers
  /// decode the JSON object into their preferred map type.
  std::vector<std::string> levels_json;
};

struct Upload {
  std::string upload_id;
  std::string state;           // "open" | "received" | "finalized"
  std::int64_t expected_size = 0;
  std::int64_t received_bytes = 0;
  std::string blob_sha;
  std::string expires_at;
};

struct ArtifactKind {
  std::string kind;
  std::string title;
  std::string description;
  bool durable = false;
};

struct StageArtifact {
  std::string artifact_id;
  std::string job_id;
  std::string task_id;
  std::string recon_id;
  std::string dataset_id;
  std::string kind;
  std::string name;
  std::string uri;
  std::string media_type;
  /// `summary_json`, `metadata_json`, and `links_json` are raw JSON
  /// objects. Keep them as strings so consumers can decode with their
  /// preferred JSON library.
  std::string summary_json;
  std::string metadata_json;
  std::string links_json;
  std::string created_at;
};

struct TaskRow {
  std::string task_id;
  std::string job_id;
  std::string kind;
  std::string status;          // pending|running|succeeded|failed|cancelled|cancelled_dirty
  std::string cache_key;
  std::string inputs_hash;
  std::string params_hash;
  /// outputs_ref is a free-form JSON object — store as a raw string
  /// here and decode with your JSON library when needed.
  std::string outputs_ref_json;
};

struct Job {
  std::string job_id;
  std::string tenant_id;
  std::string project_id;
  std::string recipe;
  std::string status;
  bool cancel_requested = false;
  bool cancel_force = false;
  std::string created_at;
  std::string started_at;
  std::string finished_at;
  std::string error_class;
  std::string error_message;
};

struct JobDetail : Job {
  std::vector<TaskRow> tasks;
};

struct ReconstructionRow {
  std::string recon_id;
  std::string project_id;
  std::string dataset_id;
  std::string dataset_snapshot_hash;
  /// `spec_json` is a free-form JSON dict.
  std::string spec_json;
  std::string rv_id;
  std::string status;
  std::string created_at;
};

struct SubModelRow {
  std::string submodel_id;
  std::string recon_id;
  std::int32_t idx = 0;
  std::string parent_submodel_id;
  std::string summary_json;
  std::string rigidity_json;
  std::optional<std::int32_t> snapshot_seq;
  std::string sealed_path;
  std::string created_at;
};

struct JobSubmitResponse {
  std::string job_id;
  std::vector<std::string> task_ids;
  std::string recon_id;        // empty when not applicable
};

struct HealthResponse {
  std::string status;
};

struct BackendVersion {
  std::string name;
  std::string version;
  std::string vendor;
  std::map<std::string, std::string> runtime_versions;
};

struct VersionResponse {
  std::string sfmapi;
  // Empty optional when no backend is registered. We use std::optional
  // here so a default-constructed VersionResponse round-trips through
  // the decoder cleanly.
  std::optional<BackendVersion> backend;
};

struct ApiKey {
  std::string api_key_id;
  std::string tenant_id;
  std::string label;
  std::string last_used_at;
  std::string revoked_at;
  std::string created_at;
};

struct ApiKeyCreated : ApiKey {
  /// Bearer token — returned only at creation time.
  std::string raw_key;
};

// ====================================================================
//  Capabilities (mirror of app/core/capabilities.py)
// ====================================================================

struct BackendInfo {
  std::string name;     // e.g. "colmap_mod"
  std::string version;
  std::string vendor;
};

/// Wire schema version of the Capabilities envelope. Bump when the
/// *shape* changes (new top-level keys, type changes); independent of
/// the feature flags themselves, which are negotiated via the dict.
constexpr int kCapabilitiesSchemaVersion = 1;

struct Capabilities {
  /// Wire schema version. See ::kCapabilitiesSchemaVersion.
  int schema_version = kCapabilitiesSchemaVersion;
  BackendInfo backend{};
  /// Flat dict from canonical capability name to bool. Absence means
  /// "unsupported"; clients MUST treat unknown keys as opaque.
  std::map<std::string, bool> features;

  bool Supports(const std::string& name) const noexcept {
    auto it = features.find(name);
    return it != features.end() && it->second;
  }
};

// ====================================================================
//  Wire-format binary readers
//    - application/x-sfm-points-v1   (44-byte header + 26B records)
//    - application/x-sfm-depth-v1    (32-byte header + W*H float32)
//    - application/x-sfm-normal-v1   (32-byte header + W*H*3 float32)
// ====================================================================

class WireFormatError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

namespace detail {
inline bool MagicEquals(const std::uint8_t* p, const std::uint8_t* expected,
                        std::size_t n) noexcept {
  return std::memcmp(p, expected, n) == 0;
}

template <typename T>
inline T LoadLE(const std::uint8_t* p) noexcept {
  T v;
  std::memcpy(&v, p, sizeof(T));
  return v;
}

inline constexpr std::uint8_t kPointsMagic[8] = {
    'S', 'F', 'M', 'P', '3', 'D', 0, 0};
inline constexpr std::uint8_t kDepthMagic[8] = {
    'S', 'F', 'M', 'D', 'P', 'T', 'H', 0};
inline constexpr std::uint8_t kNormalMagic[8] = {
    'S', 'F', 'M', 'N', 'R', 'M', 0, 0};

inline constexpr std::size_t kPointsHeaderSize = 44;
inline constexpr std::size_t kPointsRecordSize = 26;
inline constexpr std::size_t kMapHeaderSize = 32;
}  // namespace detail

struct Point3DRecord {
  std::uint64_t point3d_id = 0;
  std::array<float, 3> xyz{0.0f, 0.0f, 0.0f};
  std::array<std::uint8_t, 3> rgb{0, 0, 0};
  std::uint16_t track_len = 0;
};

struct PointsBinary {
  std::uint64_t count = 0;
  std::array<float, 3> bbox_min{0.0f, 0.0f, 0.0f};
  std::array<float, 3> bbox_max{0.0f, 0.0f, 0.0f};
  std::vector<Point3DRecord> records;
};

inline PointsBinary ParsePointsBinary(const std::uint8_t* data, std::size_t len) {
  using namespace detail;
  if (len < kPointsHeaderSize) {
    throw WireFormatError("points-binary: buffer too small for header");
  }
  if (!MagicEquals(data, kPointsMagic, 8)) {
    throw WireFormatError("points-binary: bad magic");
  }
  const auto version = LoadLE<std::uint32_t>(data + 8);
  if (version != 1) {
    throw WireFormatError("points-binary: unknown version");
  }
  PointsBinary out;
  out.count = LoadLE<std::uint64_t>(data + 12);
  for (int i = 0; i < 3; ++i) {
    out.bbox_min[i] = LoadLE<float>(data + 20 + i * 4);
    out.bbox_max[i] = LoadLE<float>(data + 32 + i * 4);
  }
  const std::size_t expected =
      kPointsHeaderSize + out.count * kPointsRecordSize;
  if (len < expected) {
    throw WireFormatError("points-binary: body short");
  }
  out.records.resize(out.count);
  for (std::uint64_t i = 0; i < out.count; ++i) {
    const std::uint8_t* p =
        data + kPointsHeaderSize + i * kPointsRecordSize;
    auto& r = out.records[i];
    r.xyz[0] = LoadLE<float>(p);
    r.xyz[1] = LoadLE<float>(p + 4);
    r.xyz[2] = LoadLE<float>(p + 8);
    r.rgb[0] = p[12];
    r.rgb[1] = p[13];
    r.rgb[2] = p[14];
    r.track_len = LoadLE<std::uint16_t>(p + 16);
    r.point3d_id = LoadLE<std::uint64_t>(p + 18);
  }
  return out;
}

struct DepthMap {
  std::uint32_t width = 0;
  std::uint32_t height = 0;
  float depth_min = 0.0f;
  float depth_max = 0.0f;
  /// Row-major float32 array of length `width * height`.
  std::vector<float> pixels;
};

inline DepthMap ParseDepthMap(const std::uint8_t* data, std::size_t len) {
  using namespace detail;
  if (len < kMapHeaderSize) {
    throw WireFormatError("depth-binary: buffer too small for header");
  }
  if (!MagicEquals(data, kDepthMagic, 8)) {
    throw WireFormatError("depth-binary: bad magic");
  }
  if (LoadLE<std::uint32_t>(data + 8) != 1) {
    throw WireFormatError("depth-binary: unknown version");
  }
  DepthMap out;
  out.width = LoadLE<std::uint32_t>(data + 12);
  out.height = LoadLE<std::uint32_t>(data + 16);
  out.depth_min = LoadLE<float>(data + 20);
  out.depth_max = LoadLE<float>(data + 24);
  const std::size_t pixel_count = static_cast<std::size_t>(out.width) * out.height;
  const std::size_t expected = kMapHeaderSize + pixel_count * 4;
  if (len < expected) {
    throw WireFormatError("depth-binary: body short");
  }
  out.pixels.resize(pixel_count);
  std::memcpy(out.pixels.data(), data + kMapHeaderSize, pixel_count * 4);
  return out;
}

struct NormalMap {
  std::uint32_t width = 0;
  std::uint32_t height = 0;
  /// Row-major channels-last float32 of length `width * height * 3`.
  std::vector<float> pixels;
};

inline NormalMap ParseNormalMap(const std::uint8_t* data, std::size_t len) {
  using namespace detail;
  if (len < kMapHeaderSize) {
    throw WireFormatError("normal-binary: buffer too small for header");
  }
  if (!MagicEquals(data, kNormalMagic, 8)) {
    throw WireFormatError("normal-binary: bad magic");
  }
  if (LoadLE<std::uint32_t>(data + 8) != 1) {
    throw WireFormatError("normal-binary: unknown version");
  }
  NormalMap out;
  out.width = LoadLE<std::uint32_t>(data + 12);
  out.height = LoadLE<std::uint32_t>(data + 16);
  const std::size_t pixel_count =
      static_cast<std::size_t>(out.width) * out.height * 3;
  const std::size_t expected = kMapHeaderSize + pixel_count * 4;
  if (len < expected) {
    throw WireFormatError("normal-binary: body short");
  }
  out.pixels.resize(pixel_count);
  std::memcpy(out.pixels.data(), data + kMapHeaderSize, pixel_count * 4);
  return out;
}

}  // namespace sfmapi

#endif  // SFMAPI_SFMAPI_HPP_
