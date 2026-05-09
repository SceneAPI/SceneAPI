"""Typed Python SDK for sfmapi.

.. deprecated:: 0.0.2
   This hand-rolled SDK is **deprecated** in favor of
   :mod:`sfmapi_client_gen` (auto-generated from the live OpenAPI
   spec by ``openapi-python-client``). The generated SDK ships with
   the same ergonomics surface — typed ``SfmApiError`` hierarchy,
   ``supports()`` / ``upload_bytes()`` / ``stream_events()`` /
   ``parse_points_binary()`` / ``wait_for_job()`` /
   ``submit_and_wait()`` — at ``sfmapi_client_gen._ergonomics``.

   Migration:

       # before:
       from sfmapi_client import SfmApiClient
       c = SfmApiClient("http://localhost:8080")

       # after:
       from sfmapi_client_gen import Client
       from sfmapi_client_gen._ergonomics import upload_bytes, wait_for_job
       c = Client(base_url="http://localhost:8080")

   This package will be removed in a future release. Existing
   imports continue to work; a single :class:`DeprecationWarning`
   is emitted on first import.

Top-level surface (legacy):

    from sfmapi_client import SfmApiClient, AsyncSfmApiClient
    from sfmapi_client.models import (
        Project, Dataset, Image, Job, Reconstruction,
        IncrementalSpec, FeaturesSpec, PairsSpec, MatcherSpec, VerifySpec,
    )
    from sfmapi_client.errors import (
        SfmApiError, NotFoundError, ConflictError,
        ValidationError, QuotaExceededError, AuthError,
    )

Both clients share the same model layer; pick the one that matches
your event loop posture.
"""

import warnings as _warnings

from sfmapi_client.binary import (
    DepthMap,
    NormalMap,
    Point3DRecord,
    PointsBinary,
    WireFormatError,
    parse_depth_map,
    parse_normal_map,
    parse_points_binary,
)
from sfmapi_client.client_async import AsyncSfmApiClient
from sfmapi_client.client_sync import SfmApiClient
from sfmapi_client.errors import (
    AuthError,
    ConflictError,
    NotFoundError,
    QuotaExceededError,
    SfmApiError,
    ValidationError,
)
from sfmapi_client.models import (
    Capabilities,
    Dataset,
    FeaturesSpec,
    GlobalSpec,
    HierarchicalSpec,
    Image,
    ImagePairRef,
    IncrementalSpec,
    Job,
    JobDetail,
    MatcherSpec,
    PairsSpec,
    PipelineSpec,
    ProgressEvent,
    Project,
    Reconstruction,
    SphericalSpec,
    SubModel,
    Upload,
    VerifySpec,
)

_warnings.warn(
    "sfmapi_client is deprecated; the package will be removed at "
    "the 0.1.0 release. Migrate to sfmapi_client_gen "
    "(auto-generated from the OpenAPI spec) plus "
    "sfmapi_client_gen._ergonomics for the helper surface. "
    "See sfmapi_client.__doc__ for migration guidance.",
    DeprecationWarning,
    stacklevel=2,
)
del _warnings

__version__ = "0.0.1"

__all__ = [
    "AsyncSfmApiClient",
    "AuthError",
    "Capabilities",
    "ConflictError",
    "Dataset",
    "DepthMap",
    "FeaturesSpec",
    "GlobalSpec",
    "HierarchicalSpec",
    "Image",
    "ImagePairRef",
    "IncrementalSpec",
    "Job",
    "JobDetail",
    "MatcherSpec",
    "NormalMap",
    "NotFoundError",
    "PairsSpec",
    "PipelineSpec",
    "Point3DRecord",
    "PointsBinary",
    "ProgressEvent",
    "Project",
    "QuotaExceededError",
    "Reconstruction",
    "SfmApiClient",
    "SfmApiError",
    "SphericalSpec",
    "SubModel",
    "Upload",
    "ValidationError",
    "VerifySpec",
    "WireFormatError",
    "__version__",
    "parse_depth_map",
    "parse_normal_map",
    "parse_points_binary",
]
