export { SfmApiClient } from "./client.js";
export type { SfmApiClientOptions, RequestOptions } from "./client.js";
export {
  SfmApiError,
  AuthError,
  NotFoundError,
  ConflictError,
  ValidationError,
  QuotaExceededError,
  StorageError,
  PycolmapUnavailableError,
  TransportError,
} from "./errors.js";
export type {
  Project,
  ProjectCreate,
  ProjectPatch,
  Dataset,
  DatasetCreate,
  DatasetPatch,
  SourceSpec,
  UploadSourceSpec,
  LocalSourceSpec,
  S3SourceSpec,
  Image,
  ImageCreate,
  BatchCreateImagesRequest,
  BatchCreateImagesResponse,
  ImageObservationRow,
  PointObservationRow,
  TilesIndex,
  ApiKey,
  ApiKeyCreated,
  Upload,
  Job,
  JobDetail,
  Task,
  Reconstruction,
  SubModel,
  Page,
  HealthResponse,
  VersionResponse,
  JobSubmitResponse,
  FeatureType,
  FeaturesSpec,
  PairStrategy,
  ImagePairRef,
  PairsSpec,
  MatcherType,
  MatcherSpec,
  VerifySpec,
  BundleAdjustmentSpec,
  IncrementalSpec,
  GlobalSpec,
  HierarchicalSpec,
  SphericalSpec,
  PipelineSpec,
  ProgressEvent,
} from "./models.js";
export type {
  // ----- scene types (mirror of app.schemas.api.scene) -----
  Rotation,
  Rigid3,
  Sim3,
  GpsCoord,
  ImuMeasurement,
  PosePrior,
  Camera,
  Point2D,
  ImagePose,
  TrackElement,
  Track,
  Rig,
  Frame,
  TwoViewGeometry,
  TwoViewGeometryType,
  CorrespondencePair,
  PoseGraphEdge,
  PoseGraph,
  CamerasFile,
  ImagesFile,
  RigsFile,
  FramesFile,
  TwoViewGeometriesFile,
  CorrespondenceGraphFile,
  PoseGraphFile,
  LocalizationResult,
  DepthMapInfo,
  DenseSummary,
  DenseManifestFile,
  MeshMethod,
  MeshSummary,
  MeshFile,
} from "./scene.js";
export { SPHERICAL_CAMERA_MODEL, isSphericalCamera } from "./scene.js";
export type {
  BackendInfo,
  Capabilities,
  CoreCapability,
  OptionalCapability,
  CanonicalCapability,
} from "./capabilities.js";
export {
  CORE_CAPABILITIES,
  OPTIONAL_CAPABILITIES,
  supports,
} from "./capabilities.js";
export type {
  Point3DRecord,
  PointsBinary,
  DepthMap,
  NormalMap,
} from "./binary.js";
export { parsePointsBinary, parseDepthMap, parseNormalMap } from "./binary.js";
export { sha256Hex } from "./hash.js";

export const VERSION = "0.0.1";

// `openapi-types.ts` is regenerated on every CI run from the live
// FastAPI app. We don't import or re-export it from the public surface
// because it's bulky (~2.3k lines) and duplicates types we hand-roll.
// It exists so the drift checker (`drift/check.ts`) can verify the
// hand-rolled types stay structurally compatible with the server's
// OpenAPI schema. To inspect it, run `npm run gen:openapi-types`.
