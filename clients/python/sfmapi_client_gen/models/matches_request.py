from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.matcher_spec import MatcherSpec
    from ..models.matches_request_input_artifacts import MatchesRequestInputArtifacts
    from ..models.pairs_spec import PairsSpec


T = TypeVar("T", bound="MatchesRequest")


@_attrs_define
class MatchesRequest:
    """Match-stage request body — pair selection + per-pair matcher
    are independent shapes (AIP-202: one concept per type).

        Attributes:
            pairs (PairsSpec | Unset): Pair-selection strategy. Decoupled from the matcher so the
                standard supports "select pairs with hloc-style retrieval, then
                match with any local-feature matcher" workflows.

                Capability flag is ``pairs.{strategy}``.
            matcher (MatcherSpec | Unset): Per-pair feature matcher.

                ``nn-mutual`` is the COLMAP default (mutual nearest-neighbor).
                ``nn-ratio`` adds Lowe's ratio test. ``superglue`` / ``lightglue``
                are learned matchers. ``loftr`` is semi-dense (no separate
                extractor — set ``FeaturesSpec.type`` to a placeholder).
            input_artifacts (MatchesRequestInputArtifacts | Unset):
    """

    pairs: PairsSpec | Unset = UNSET
    matcher: MatcherSpec | Unset = UNSET
    input_artifacts: MatchesRequestInputArtifacts | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        pairs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.pairs, Unset):
            pairs = self.pairs.to_dict()

        matcher: dict[str, Any] | Unset = UNSET
        if not isinstance(self.matcher, Unset):
            matcher = self.matcher.to_dict()

        input_artifacts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_artifacts, Unset):
            input_artifacts = self.input_artifacts.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if pairs is not UNSET:
            field_dict["pairs"] = pairs
        if matcher is not UNSET:
            field_dict["matcher"] = matcher
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.matcher_spec import MatcherSpec
        from ..models.matches_request_input_artifacts import MatchesRequestInputArtifacts
        from ..models.pairs_spec import PairsSpec

        d = dict(src_dict)
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

        _input_artifacts = d.pop("input_artifacts", UNSET)
        input_artifacts: MatchesRequestInputArtifacts | Unset
        if isinstance(_input_artifacts, Unset):
            input_artifacts = UNSET
        else:
            input_artifacts = MatchesRequestInputArtifacts.from_dict(_input_artifacts)

        matches_request = cls(
            pairs=pairs,
            matcher=matcher,
            input_artifacts=input_artifacts,
        )

        return matches_request
