from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.features_spec import FeaturesSpec
    from ..models.global_spec import GlobalSpec
    from ..models.hierarchical_spec import HierarchicalSpec
    from ..models.incremental_spec import IncrementalSpec
    from ..models.matcher_spec import MatcherSpec
    from ..models.pairs_spec import PairsSpec
    from ..models.pipeline_request_input_artifacts import PipelineRequestInputArtifacts
    from ..models.spherical_spec import SphericalSpec
    from ..models.verify_spec import VerifySpec


T = TypeVar("T", bound="PipelineRequest")


@_attrs_define
class PipelineRequest:
    """End-to-end pipeline request — features + pair selection +
    matcher + two-view verification + mapping spec, sent in one body
    for the recipe routes (``/pipelines/{incremental|global|...}``).

    Pair selection (``pairs``) and per-pair matching (``matcher``) are
    independent shapes (AIP-202).

        Attributes:
            dataset_id (str):
            spec (GlobalSpec | HierarchicalSpec | IncrementalSpec | SphericalSpec):
            features (FeaturesSpec | Unset): Type-tagged feature extractor request.

                Backends report which ``type`` values they support via the
                ``features.extract.{type}`` capability flags. Unsupported types
                return 501 with the canonical capability name.

                Backwards compat: the legacy ``sift_max_num_features`` /
                ``sift_first_octave`` fields are accepted as aliases when
                ``type=="sift"``.
            pairs (PairsSpec | Unset): Pair-selection strategy. Decoupled from the matcher so the
                standard supports "select pairs with hloc-style retrieval, then
                match with any local-feature matcher" workflows.

                Capability flag is ``pairs.{strategy}``.
            matcher (MatcherSpec | Unset): Per-pair feature matcher.

                ``nn-mutual`` is the COLMAP default (mutual nearest-neighbor).
                ``nn-ratio`` adds Lowe's ratio test. ``superglue`` / ``lightglue``
                are learned matchers. ``loftr`` is semi-dense (no separate
                extractor — set ``FeaturesSpec.type`` to a placeholder).
            verify (VerifySpec | Unset):
            input_artifacts (PipelineRequestInputArtifacts | Unset): Optional role-keyed artifact references shared by the
                recipe. Stage-local input_artifacts on features, pairs, matcher, verify, or spec are merged with this map.
    """

    dataset_id: str
    spec: GlobalSpec | HierarchicalSpec | IncrementalSpec | SphericalSpec
    features: FeaturesSpec | Unset = UNSET
    pairs: PairsSpec | Unset = UNSET
    matcher: MatcherSpec | Unset = UNSET
    verify: VerifySpec | Unset = UNSET
    input_artifacts: PipelineRequestInputArtifacts | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.global_spec import GlobalSpec
        from ..models.hierarchical_spec import HierarchicalSpec
        from ..models.incremental_spec import IncrementalSpec

        dataset_id = self.dataset_id

        spec: dict[str, Any]
        if (
            isinstance(self.spec, IncrementalSpec)
            or isinstance(self.spec, GlobalSpec)
            or isinstance(self.spec, HierarchicalSpec)
        ):
            spec = self.spec.to_dict()
        else:
            spec = self.spec.to_dict()

        features: dict[str, Any] | Unset = UNSET
        if not isinstance(self.features, Unset):
            features = self.features.to_dict()

        pairs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.pairs, Unset):
            pairs = self.pairs.to_dict()

        matcher: dict[str, Any] | Unset = UNSET
        if not isinstance(self.matcher, Unset):
            matcher = self.matcher.to_dict()

        verify: dict[str, Any] | Unset = UNSET
        if not isinstance(self.verify, Unset):
            verify = self.verify.to_dict()

        input_artifacts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_artifacts, Unset):
            input_artifacts = self.input_artifacts.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dataset_id": dataset_id,
                "spec": spec,
            }
        )
        if features is not UNSET:
            field_dict["features"] = features
        if pairs is not UNSET:
            field_dict["pairs"] = pairs
        if matcher is not UNSET:
            field_dict["matcher"] = matcher
        if verify is not UNSET:
            field_dict["verify"] = verify
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.features_spec import FeaturesSpec
        from ..models.global_spec import GlobalSpec
        from ..models.hierarchical_spec import HierarchicalSpec
        from ..models.incremental_spec import IncrementalSpec
        from ..models.matcher_spec import MatcherSpec
        from ..models.pairs_spec import PairsSpec
        from ..models.pipeline_request_input_artifacts import PipelineRequestInputArtifacts
        from ..models.spherical_spec import SphericalSpec
        from ..models.verify_spec import VerifySpec

        d = dict(src_dict)
        dataset_id = d.pop("dataset_id")

        def _parse_spec(
            data: object,
        ) -> GlobalSpec | HierarchicalSpec | IncrementalSpec | SphericalSpec:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                spec_type_0 = IncrementalSpec.from_dict(data)

                return spec_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                spec_type_1 = GlobalSpec.from_dict(data)

                return spec_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                spec_type_2 = HierarchicalSpec.from_dict(data)

                return spec_type_2
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            spec_type_3 = SphericalSpec.from_dict(data)

            return spec_type_3

        spec = _parse_spec(d.pop("spec"))

        _features = d.pop("features", UNSET)
        features: FeaturesSpec | Unset
        if isinstance(_features, Unset):
            features = UNSET
        else:
            features = FeaturesSpec.from_dict(_features)

        _pairs = d.pop("pairs", UNSET)
        pairs: PairsSpec | Unset
        if isinstance(_pairs, Unset):
            pairs = UNSET
        else:
            pairs = PairsSpec.from_dict(_pairs)

        _matcher = d.pop("matcher", UNSET)
        matcher: MatcherSpec | Unset
        if isinstance(_matcher, Unset):
            matcher = UNSET
        else:
            matcher = MatcherSpec.from_dict(_matcher)

        _verify = d.pop("verify", UNSET)
        verify: VerifySpec | Unset
        if isinstance(_verify, Unset):
            verify = UNSET
        else:
            verify = VerifySpec.from_dict(_verify)

        _input_artifacts = d.pop("input_artifacts", UNSET)
        input_artifacts: PipelineRequestInputArtifacts | Unset
        if isinstance(_input_artifacts, Unset):
            input_artifacts = UNSET
        else:
            input_artifacts = PipelineRequestInputArtifacts.from_dict(_input_artifacts)

        pipeline_request = cls(
            dataset_id=dataset_id,
            spec=spec,
            features=features,
            pairs=pairs,
            matcher=matcher,
            verify=verify,
            input_artifacts=input_artifacts,
        )

        pipeline_request.additional_properties = d
        return pipeline_request

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
