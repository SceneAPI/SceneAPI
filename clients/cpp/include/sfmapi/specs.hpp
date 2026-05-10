// sfmapi — pipeline / stage spec POD structs for C++.
//
// The C++ SDK historically asked callers to build JSON bodies as
// strings. These structs give parity with Python / TypeScript: build
// a `FeaturesSpec`/`MatcherSpec`/etc. and call `.ToJson()` to get a
// `sfmapi::Json` value (or `.ToJsonString()` for the wire body).
//
// Field layout exactly mirrors `app/schemas/pipeline_spec.py` and
// `clients/python/sfmapi_client/models.py`.

#ifndef SFMAPI_SPECS_HPP_
#define SFMAPI_SPECS_HPP_

#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "sfmapi/json.hpp"

namespace sfmapi {

// ====================================================================
//  Stage artifact inputs
// ====================================================================

struct ArtifactRef {
  std::string artifact_id;
  std::optional<std::string> kind;

  Json ToJson() const {
    Json::Object o{{"artifact_id", artifact_id}};
    if (kind) {
      o["kind"] = *kind;
    }
    return Json(std::move(o));
  }
};

using ArtifactInputMap = std::map<std::string, ArtifactRef>;

inline void AddInputArtifacts(Json::Object& o,
                              const ArtifactInputMap& input_artifacts) {
  if (input_artifacts.empty()) {
    return;
  }
  Json::Object refs;
  for (const auto& kv : input_artifacts) {
    refs[kv.first] = kv.second.ToJson();
  }
  o["input_artifacts"] = Json(std::move(refs));
}

// ====================================================================
//  Local-feature extractor
// ====================================================================

/// Canonical extractor types — mirrors `FeatureType`.
constexpr const char* kFeatureTypeSift = "sift";
constexpr const char* kFeatureTypeSuperPoint = "superpoint";
constexpr const char* kFeatureTypeAliked = "aliked";
constexpr const char* kFeatureTypeDisk = "disk";
constexpr const char* kFeatureTypeR2D2 = "r2d2";
constexpr const char* kFeatureTypeD2Net = "d2net";

struct FeaturesSpec {
  int version = 1;
  std::string type = kFeatureTypeSift;
  std::optional<std::string> provider;
  int max_num_features = 8192;
  bool use_gpu = true;
  int seed = 0;
  Json::Object backend_options;
  Json::Object extractor_options;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"version", static_cast<double>(version)},
        {"type", type},
        {"max_num_features", static_cast<double>(max_num_features)},
        {"use_gpu", use_gpu},
        {"seed", static_cast<double>(seed)},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    if (!extractor_options.empty()) {
      o["extractor_options"] = Json(extractor_options);
    }
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }

  std::string ToJsonString() const { return ToJson().Dump(); }
};

// ====================================================================
//  Pair-selection
// ====================================================================

constexpr const char* kPairExhaustive = "exhaustive";
constexpr const char* kPairSequential = "sequential";
constexpr const char* kPairSpatial = "spatial";
constexpr const char* kPairVocabTree = "vocabtree";
constexpr const char* kPairRetrieval = "retrieval";
constexpr const char* kPairFromPoses = "from_poses";

struct PairsSpec {
  int version = 1;
  std::string strategy = kPairExhaustive;
  std::optional<std::string> provider;
  int overlap = 10;
  std::optional<std::string> vocab_tree_path;
  std::string retrieval_strategy = "vlad";  // dhash | vlad | netvlad
  int retrieval_k = 20;
  std::optional<double> overlap_distance_m;
  std::optional<double> max_angle_deg;
  Json::Object backend_options;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"version", static_cast<double>(version)},
        {"strategy", strategy},
        {"overlap", static_cast<double>(overlap)},
        {"retrieval_strategy", retrieval_strategy},
        {"retrieval_k", static_cast<double>(retrieval_k)},
    };
    if (provider) o["provider"] = *provider;
    if (vocab_tree_path) o["vocab_tree_path"] = *vocab_tree_path;
    if (overlap_distance_m) o["overlap_distance_m"] = *overlap_distance_m;
    if (max_angle_deg) o["max_angle_deg"] = *max_angle_deg;
    if (!backend_options.empty()) o["backend_options"] = Json(backend_options);
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

// ====================================================================
//  Matcher
// ====================================================================

constexpr const char* kMatcherNnMutual = "nn-mutual";
constexpr const char* kMatcherNnRatio = "nn-ratio";
constexpr const char* kMatcherSuperGlue = "superglue";
constexpr const char* kMatcherLightGlue = "lightglue";
constexpr const char* kMatcherLoFTR = "loftr";
constexpr const char* kMatcherMASt3R = "mast3r";

struct MatcherSpec {
  int version = 1;
  std::string type = kMatcherNnMutual;
  std::optional<std::string> provider;
  bool use_gpu = true;
  bool cross_check = true;
  double max_ratio = 0.8;
  double max_distance = 0.7;
  Json::Object backend_options;
  Json::Object matcher_options;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"version", static_cast<double>(version)},
        {"type", type},
        {"use_gpu", use_gpu},
        {"cross_check", cross_check},
        {"max_ratio", max_ratio},
        {"max_distance", max_distance},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    if (!matcher_options.empty()) {
      o["matcher_options"] = Json(matcher_options);
    }
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

// ====================================================================
//  Verify + BA
// ====================================================================

struct VerifySpec {
  int version = 1;
  std::optional<std::string> provider;
  bool use_gpu = true;
  double min_inlier_ratio = 0.25;
  Json::Object backend_options;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"version", static_cast<double>(version)},
        {"use_gpu", use_gpu},
        {"min_inlier_ratio", min_inlier_ratio},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

constexpr const char* kBaModeStandard = "standard";
constexpr const char* kBaModeTwoStage = "two_stage";
constexpr const char* kBaModeFeaturemetric = "featuremetric";

constexpr const char* kBaLossSquared = "squared";
constexpr const char* kBaLossHuber = "huber";
constexpr const char* kBaLossCauchy = "cauchy";
constexpr const char* kBaLossSoftL1 = "soft_l1";
constexpr const char* kBaLossTukey = "tukey";

struct BundleAdjustmentSpec {
  int version = 1;
  std::string mode = kBaModeStandard;
  std::optional<std::string> provider;
  bool refine_focal_length = true;
  bool refine_principal_point = false;
  bool refine_extra_params = true;
  int max_num_iterations = 100;
  std::string loss_kernel = kBaLossSquared;
  double loss_threshold = 1.0;
  Json::Object backend_options;

  Json ToJson() const {
    Json::Object o{
        {"version", static_cast<double>(version)},
        {"mode", mode},
        {"refine_focal_length", refine_focal_length},
        {"refine_principal_point", refine_principal_point},
        {"refine_extra_params", refine_extra_params},
        {"max_num_iterations", static_cast<double>(max_num_iterations)},
        {"loss_kernel", loss_kernel},
        {"loss_threshold", loss_threshold},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

// ====================================================================
//  Mapping pipeline specs (discriminated by `kind`)
// ====================================================================

struct IncrementalSpec {
  std::string kind = "incremental";
  int version = 1;
  std::optional<std::string> provider;
  int seed = 0;
  std::optional<int> max_runtime_seconds;
  std::optional<int> snapshot_frames_freq = 50;
  Json::Object backend_options;
  std::optional<std::pair<std::string, std::string>> init_image_pair;
  bool multiple_models = true;
  int max_num_models = 50;
  int min_num_matches = 15;
  bool extract_colors = true;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"kind", kind},
        {"version", static_cast<double>(version)},
        {"seed", static_cast<double>(seed)},
        {"multiple_models", multiple_models},
        {"max_num_models", static_cast<double>(max_num_models)},
        {"min_num_matches", static_cast<double>(min_num_matches)},
        {"extract_colors", extract_colors},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    if (max_runtime_seconds) {
      o["max_runtime_seconds"] = static_cast<double>(*max_runtime_seconds);
    }
    if (snapshot_frames_freq) {
      o["snapshot_frames_freq"] = static_cast<double>(*snapshot_frames_freq);
    }
    if (init_image_pair) {
      Json::Array p{init_image_pair->first, init_image_pair->second};
      o["init_image_pair"] = Json(std::move(p));
    }
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

struct GlobalSpec {
  std::string kind = "global";
  int version = 1;
  std::optional<std::string> provider;
  int seed = 0;
  std::string backend = "AUTO";
  std::string formulation = "AUTO";
  bool use_incremental_quality_fallback = true;
  Json::Object backend_options;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"kind", kind},
        {"version", static_cast<double>(version)},
        {"seed", static_cast<double>(seed)},
        {"backend", backend},
        {"formulation", formulation},
        {"use_incremental_quality_fallback", use_incremental_quality_fallback},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

struct HierarchicalSpec {
  std::string kind = "hierarchical";
  int version = 1;
  std::optional<std::string> provider;
  int seed = 0;
  int cluster_max_size = 100;
  int cluster_overlap = 25;
  Json::Object backend_options;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"kind", kind},
        {"version", static_cast<double>(version)},
        {"seed", static_cast<double>(seed)},
        {"cluster_max_size", static_cast<double>(cluster_max_size)},
        {"cluster_overlap", static_cast<double>(cluster_overlap)},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

struct SphericalSpec {
  std::string kind = "spherical";
  int version = 1;
  std::optional<std::string> provider;
  int seed = 0;
  bool panorama = true;
  Json::Object backend_options;
  ArtifactInputMap input_artifacts;

  Json ToJson() const {
    Json::Object o{
        {"kind", kind},
        {"version", static_cast<double>(version)},
        {"seed", static_cast<double>(seed)},
        {"panorama", panorama},
    };
    if (provider) {
      o["provider"] = *provider;
    }
    if (!backend_options.empty()) {
      o["backend_options"] = Json(backend_options);
    }
    AddInputArtifacts(o, input_artifacts);
    return Json(std::move(o));
  }
  std::string ToJsonString() const { return ToJson().Dump(); }
};

}  // namespace sfmapi

#endif  // SFMAPI_SPECS_HPP_
