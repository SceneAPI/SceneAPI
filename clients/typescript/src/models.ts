// Type-only mirror of the server schemas. Kept here so the SDK ships
// without depending on any runtime validator.

export interface Page<T> {
  items: T[];
  next_page_token: string | null;
  total: number | null;
}

export interface ProjectCreate {
  name: string;
  description?: string | null;
}

export interface ProjectPatch {
  /** Partial update — unset fields are left untouched. */
  name?: string;
  description?: string | null;
}

export interface Project {
  project_id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at?: string | null;
  _links?: Record<string, { href?: string | null } | null> | null;
}

export interface UploadSourceSpec {
  kind: "upload";
  entries: Array<{ name: string; blob_sha: string }>;
}

export interface LocalSourceSpec {
  kind: "local";
  root: string;
  recursive?: boolean;
}

export interface S3SourceSpec {
  kind: "s3";
  bucket: string;
  prefix: string;
}

export type SourceSpec = UploadSourceSpec | LocalSourceSpec | S3SourceSpec;

export interface DatasetCreate {
  name: string;
  source: SourceSpec;
  camera_model?: string;
  intrinsics_mode?: "single_camera" | "per_image" | "per_folder";
  is_spherical?: boolean;
  rig_config?: Record<string, unknown> | null;
  respect_exif_orientation?: boolean;
}

export interface DatasetPatch {
  name?: string;
  camera_model?: string;
  intrinsics_mode?: "single_camera" | "per_image" | "per_folder";
  is_spherical?: boolean;
  rig_config?: Record<string, unknown> | null;
  respect_exif_orientation?: boolean;
  active_maskset_id?: string | null;
}

export interface ImageCreate {
  name: string;
  blob_sha?: string | null;
  rel_path?: string | null;
  width?: number | null;
  height?: number | null;
  exif?: Record<string, unknown> | null;
}

export interface BatchCreateImagesRequest {
  requests: ImageCreate[];
}

export interface BatchCreateImagesResponse {
  images: Image[];
}

export interface ImageObservationRow {
  point3d_id: number;
  x: number;
  y: number;
  kp_idx?: number;
  error?: number | null;
}

export interface PointObservationRow {
  image_id: number;
  x: number;
  y: number;
  kp_idx?: number;
}

export interface TilesIndex {
  bbox_min: number[];
  bbox_max: number[];
  levels: Array<Record<string, unknown>>;
}

export interface ApiKey {
  api_key_id: string;
  tenant_id: string;
  label?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  /** Bearer token — returned only at creation time. */
  raw_key: string;
}

export interface Dataset {
  dataset_id: string;
  tenant_id: string;
  project_id: string;
  source_id: string;
  name: string;
  camera_model: string;
  intrinsics_mode: string;
  is_spherical: boolean;
  respect_exif_orientation: boolean;
  /**
   * Server emits the column name (`rig_config_json`) directly. We mirror
   * the wire shape so JSON round-trips don't require renaming.
   */
  rig_config_json: Record<string, unknown> | null;
  active_maskset_id: string | null;
  manifest_hash: string;
  created_at: string;
  updated_at?: string | null;
  _links?: Record<string, { href?: string | null } | null> | null;
}

export interface Image {
  image_id: string;
  dataset_id: string;
  name: string;
  content_sha: string;
  source_kind: string;
  rel_path: string | null;
  byte_size: number | null;
  width: number | null;
  height: number | null;
  created_at: string;
  _links?: Record<string, { href?: string | null } | null> | null;
}

export interface Upload {
  upload_id: string;
  state: "open" | "received" | "finalized";
  expected_size: number;
  received_bytes: number;
  blob_sha: string | null;
  expires_at: string;
}

export interface Task {
  task_id: string;
  job_id: string;
  kind: string;
  status:
    | "pending"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "cancelled_dirty";
  cache_key: string;
  inputs_hash: string;
  params_hash: string;
  outputs_ref: Record<string, unknown> | null;
}

export interface Job {
  job_id: string;
  tenant_id: string;
  project_id: string;
  recipe: string;
  status: Task["status"];
  cancel_requested: boolean;
  cancel_force: boolean;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_class?: string | null;
  error_message?: string | null;
  _links?: Record<string, { href?: string | null } | null> | null;
}

export interface JobDetail extends Job {
  tasks: Task[];
}

export interface Reconstruction {
  recon_id: string;
  project_id: string;
  dataset_id: string;
  dataset_snapshot_hash: string;
  spec: Record<string, unknown>;
  rv_id: string;
  status: string;
  created_at: string;
  _links?: Record<string, { href?: string | null } | null> | null;
}

export interface SubModel {
  submodel_id: string;
  recon_id: string;
  idx: number;
  parent_submodel_id: string | null;
  summary: Record<string, unknown> | null;
  rigidity: Record<string, unknown> | null;
  snapshot_seq: number | null;
  sealed_path: string | null;
  created_at: string;
  _links?: Record<string, { href?: string | null } | null> | null;
}

export interface JobSubmitResponse {
  job_id: string;
  task_ids: string[];
  recon_id?: string | null;
}

export interface HealthResponse {
  status: string;
}

export interface VersionResponse {
  sfmapi: string;
  pycolmap_available: boolean;
  colmap_sha: string;
  baxx_sha: string;
  cudss_ver: string;
  cuda_arch: string;
  sam_model_sha: string;
}

// ----- Pipeline + stage specs ------------------------------------------------

/** Canonical local-feature extractors. Capability flag is
 * `features.extract.{type}`. Backends advertise the subset they
 * implement. */
export type FeatureType =
  | "sift"
  | "superpoint"
  | "aliked"
  | "disk"
  | "r2d2"
  | "d2net";

export interface FeaturesSpec {
  version?: 1;
  type?: FeatureType;
  max_num_features?: number;
  use_gpu?: boolean;
  seed?: number;
  /** Extractor-specific options forwarded to the backend. */
  extractor_options?: Record<string, unknown>;
  /** Backwards-compat aliases — only meaningful when `type === "sift"`. */
  sift_max_num_features?: number;
  sift_first_octave?: number;
}

/** Pair-selection strategy. Capability flag is `pairs.{strategy}`. */
export type PairStrategy =
  | "exhaustive"
  | "sequential"
  | "spatial"
  | "vocabtree"
  | "retrieval"
  | "from_poses";

export interface PairsSpec {
  version?: 1;
  strategy?: PairStrategy;
  overlap?: number;
  vocab_tree_path?: string | null;
  retrieval_strategy?: "dhash" | "vlad" | "netvlad";
  retrieval_k?: number;
  overlap_distance_m?: number | null;
  max_angle_deg?: number | null;
}

/** Per-pair matcher. Capability flag is `matchers.{type}`. */
export type MatcherType =
  | "nn-mutual"
  | "nn-ratio"
  | "superglue"
  | "lightglue"
  | "loftr"
  | "mast3r";

export interface MatcherSpec {
  version?: 1;
  type?: MatcherType;
  use_gpu?: boolean;
  cross_check?: boolean;
  max_ratio?: number;
  max_distance?: number;
  matcher_options?: Record<string, unknown>;
}

export interface VerifySpec {
  version?: 1;
  use_gpu?: boolean;
  min_inlier_ratio?: number;
}

export interface BundleAdjustmentSpec {
  version?: 1;
  /** `featuremetric` requires capability `ba.featuremetric`. */
  mode?: "standard" | "two_stage" | "featuremetric";
  refine_focal_length?: boolean;
  refine_principal_point?: boolean;
  refine_extra_params?: boolean;
  max_num_iterations?: number;
  loss_kernel?: "squared" | "huber" | "cauchy" | "soft_l1" | "tukey";
  loss_threshold?: number;
}

interface _PipelineSpecBase {
  version?: 1;
  seed?: number;
  max_runtime_seconds?: number | null;
  snapshot_frames_freq?: number | null;
}

export interface IncrementalSpec extends _PipelineSpecBase {
  kind: "incremental";
  init_image_pair?: [string, string] | null;
  multiple_models?: boolean;
  max_num_models?: number;
  min_num_matches?: number;
  extract_colors?: boolean;
}

export interface GlobalSpec extends _PipelineSpecBase {
  kind: "global";
  backend?: "AUTO" | "BAXX" | "CERES";
  formulation?: "AUTO" | "EXPLICIT_SCALE" | "ELIMINATED_SCALE";
  use_incremental_quality_fallback?: boolean;
}

export interface HierarchicalSpec extends _PipelineSpecBase {
  kind: "hierarchical";
  cluster_max_size?: number;
  cluster_overlap?: number;
}

export interface SphericalSpec extends _PipelineSpecBase {
  kind: "spherical";
  panorama?: boolean;
}

export type PipelineSpec =
  | IncrementalSpec
  | GlobalSpec
  | HierarchicalSpec
  | SphericalSpec;

// ----- ProgressEvent (loose union) ------------------------------------------

export type ProgressEventKind =
  | "phase_started"
  | "phase_progress"
  | "phase_completed"
  | "metric"
  | "snapshot_available"
  | "log_line"
  | "warning"
  | "error";

export interface ProgressEvent {
  schema_version: 1;
  ts: string;
  job_id: string;
  task_id?: string | null;
  seq: number;
  kind: ProgressEventKind;
  phase?: string;
  current?: number;
  total?: number | null;
  rate?: number | null;
  key?: string;
  value?: number;
  snapshot_seq?: number;
  summary?: Record<string, unknown>;
  level?: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  message?: string;
  error_class?: string;
  detail?: Record<string, unknown> | null;
}
