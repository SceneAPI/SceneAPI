from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pairs_spec_retrieval_strategy import PairsSpecRetrievalStrategy
from ..models.pairs_spec_strategy import PairsSpecStrategy
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.image_pair_ref import ImagePairRef
    from ..models.pairs_spec_backend_options import PairsSpecBackendOptions
    from ..models.pairs_spec_input_artifacts import PairsSpecInputArtifacts


T = TypeVar("T", bound="PairsSpec")


@_attrs_define
class PairsSpec:
    """Pair-selection strategy. Decoupled from the matcher so the
    standard supports "select pairs with hloc-style retrieval, then
    match with any local-feature matcher" workflows.

    Capability flag is ``pairs.{strategy}``.

        Attributes:
            version (Literal[1] | Unset):  Default: 1.
            strategy (PairsSpecStrategy | Unset):  Default: PairsSpecStrategy.EXHAUSTIVE.
            provider (None | str | Unset): Optional backend implementation selector for this pair-selection stage. Use only
                to disambiguate providers that expose the same portable pair capability.
            overlap (int | Unset):  Default: 10.
            vocab_tree_path (None | str | Unset):
            retrieval_strategy (PairsSpecRetrievalStrategy | Unset):  Default: PairsSpecRetrievalStrategy.VLAD.
            retrieval_k (int | Unset):  Default: 20.
            overlap_distance_m (float | None | Unset):
            max_angle_deg (float | None | Unset):
            image_pairs (list[ImagePairRef] | None | Unset): Inline image-name pairs for strategy='explicit'. Intended for
                small lists; upload large hloc/COLMAP pair files and pass pairs_blob_sha instead.
            pairs_blob_sha (None | str | Unset): Sha256 of a finalized upload containing newline-delimited image name pairs,
                one 'image1 image2' pair per line. Only valid with strategy='explicit'.
            pairs_blob_format (Literal['image_name_pairs_txt'] | Unset):  Default: 'image_name_pairs_txt'.
            backend_options (PairsSpecBackendOptions | Unset): Backend-specific pair-selection options. Discover supported
                keys with GET /v1/backend/config-schemas.
            input_artifacts (PairsSpecInputArtifacts | Unset): Optional role-keyed input artifact references. Use role
                'pairs' for a previously generated pair-selection artifact.
    """

    version: Literal[1] | Unset = 1
    strategy: PairsSpecStrategy | Unset = PairsSpecStrategy.EXHAUSTIVE
    provider: None | str | Unset = UNSET
    overlap: int | Unset = 10
    vocab_tree_path: None | str | Unset = UNSET
    retrieval_strategy: PairsSpecRetrievalStrategy | Unset = PairsSpecRetrievalStrategy.VLAD
    retrieval_k: int | Unset = 20
    overlap_distance_m: float | None | Unset = UNSET
    max_angle_deg: float | None | Unset = UNSET
    image_pairs: list[ImagePairRef] | None | Unset = UNSET
    pairs_blob_sha: None | str | Unset = UNSET
    pairs_blob_format: Literal["image_name_pairs_txt"] | Unset = "image_name_pairs_txt"
    backend_options: PairsSpecBackendOptions | Unset = UNSET
    input_artifacts: PairsSpecInputArtifacts | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        strategy: str | Unset = UNSET
        if not isinstance(self.strategy, Unset):
            strategy = self.strategy.value

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        overlap = self.overlap

        vocab_tree_path: None | str | Unset
        if isinstance(self.vocab_tree_path, Unset):
            vocab_tree_path = UNSET
        else:
            vocab_tree_path = self.vocab_tree_path

        retrieval_strategy: str | Unset = UNSET
        if not isinstance(self.retrieval_strategy, Unset):
            retrieval_strategy = self.retrieval_strategy.value

        retrieval_k = self.retrieval_k

        overlap_distance_m: float | None | Unset
        if isinstance(self.overlap_distance_m, Unset):
            overlap_distance_m = UNSET
        else:
            overlap_distance_m = self.overlap_distance_m

        max_angle_deg: float | None | Unset
        if isinstance(self.max_angle_deg, Unset):
            max_angle_deg = UNSET
        else:
            max_angle_deg = self.max_angle_deg

        image_pairs: list[dict[str, Any]] | None | Unset
        if isinstance(self.image_pairs, Unset):
            image_pairs = UNSET
        elif isinstance(self.image_pairs, list):
            image_pairs = []
            for image_pairs_type_0_item_data in self.image_pairs:
                image_pairs_type_0_item = image_pairs_type_0_item_data.to_dict()
                image_pairs.append(image_pairs_type_0_item)

        else:
            image_pairs = self.image_pairs

        pairs_blob_sha: None | str | Unset
        if isinstance(self.pairs_blob_sha, Unset):
            pairs_blob_sha = UNSET
        else:
            pairs_blob_sha = self.pairs_blob_sha

        pairs_blob_format = self.pairs_blob_format

        backend_options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.backend_options, Unset):
            backend_options = self.backend_options.to_dict()

        input_artifacts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_artifacts, Unset):
            input_artifacts = self.input_artifacts.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if version is not UNSET:
            field_dict["version"] = version
        if strategy is not UNSET:
            field_dict["strategy"] = strategy
        if provider is not UNSET:
            field_dict["provider"] = provider
        if overlap is not UNSET:
            field_dict["overlap"] = overlap
        if vocab_tree_path is not UNSET:
            field_dict["vocab_tree_path"] = vocab_tree_path
        if retrieval_strategy is not UNSET:
            field_dict["retrieval_strategy"] = retrieval_strategy
        if retrieval_k is not UNSET:
            field_dict["retrieval_k"] = retrieval_k
        if overlap_distance_m is not UNSET:
            field_dict["overlap_distance_m"] = overlap_distance_m
        if max_angle_deg is not UNSET:
            field_dict["max_angle_deg"] = max_angle_deg
        if image_pairs is not UNSET:
            field_dict["image_pairs"] = image_pairs
        if pairs_blob_sha is not UNSET:
            field_dict["pairs_blob_sha"] = pairs_blob_sha
        if pairs_blob_format is not UNSET:
            field_dict["pairs_blob_format"] = pairs_blob_format
        if backend_options is not UNSET:
            field_dict["backend_options"] = backend_options
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.image_pair_ref import ImagePairRef
        from ..models.pairs_spec_backend_options import PairsSpecBackendOptions
        from ..models.pairs_spec_input_artifacts import PairsSpecInputArtifacts

        d = dict(src_dict)
        version = cast(Literal[1] | Unset, d.pop("version", UNSET))
        if version != 1 and not isinstance(version, Unset):
            raise ValueError(f"version must match const 1, got '{version}'")

        _strategy = d.pop("strategy", UNSET)
        strategy: PairsSpecStrategy | Unset
        if isinstance(_strategy, Unset):
            strategy = UNSET
        else:
            strategy = PairsSpecStrategy(_strategy)

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        overlap = d.pop("overlap", UNSET)

        def _parse_vocab_tree_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        vocab_tree_path = _parse_vocab_tree_path(d.pop("vocab_tree_path", UNSET))

        _retrieval_strategy = d.pop("retrieval_strategy", UNSET)
        retrieval_strategy: PairsSpecRetrievalStrategy | Unset
        if isinstance(_retrieval_strategy, Unset):
            retrieval_strategy = UNSET
        else:
            retrieval_strategy = PairsSpecRetrievalStrategy(_retrieval_strategy)

        retrieval_k = d.pop("retrieval_k", UNSET)

        def _parse_overlap_distance_m(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        overlap_distance_m = _parse_overlap_distance_m(d.pop("overlap_distance_m", UNSET))

        def _parse_max_angle_deg(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        max_angle_deg = _parse_max_angle_deg(d.pop("max_angle_deg", UNSET))

        def _parse_image_pairs(data: object) -> list[ImagePairRef] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                image_pairs_type_0 = []
                _image_pairs_type_0 = data
                for image_pairs_type_0_item_data in _image_pairs_type_0:
                    image_pairs_type_0_item = ImagePairRef.from_dict(image_pairs_type_0_item_data)

                    image_pairs_type_0.append(image_pairs_type_0_item)

                return image_pairs_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ImagePairRef] | None | Unset, data)

        image_pairs = _parse_image_pairs(d.pop("image_pairs", UNSET))

        def _parse_pairs_blob_sha(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pairs_blob_sha = _parse_pairs_blob_sha(d.pop("pairs_blob_sha", UNSET))

        pairs_blob_format = cast(
            Literal["image_name_pairs_txt"] | Unset, d.pop("pairs_blob_format", UNSET)
        )
        if pairs_blob_format != "image_name_pairs_txt" and not isinstance(pairs_blob_format, Unset):
            raise ValueError(
                f"pairs_blob_format must match const 'image_name_pairs_txt', got '{pairs_blob_format}'"
            )

        _backend_options = d.pop("backend_options", UNSET)
        backend_options: PairsSpecBackendOptions | Unset
        if isinstance(_backend_options, Unset):
            backend_options = UNSET
        else:
            backend_options = PairsSpecBackendOptions.from_dict(_backend_options)

        _input_artifacts = d.pop("input_artifacts", UNSET)
        input_artifacts: PairsSpecInputArtifacts | Unset
        if isinstance(_input_artifacts, Unset):
            input_artifacts = UNSET
        else:
            input_artifacts = PairsSpecInputArtifacts.from_dict(_input_artifacts)

        pairs_spec = cls(
            version=version,
            strategy=strategy,
            provider=provider,
            overlap=overlap,
            vocab_tree_path=vocab_tree_path,
            retrieval_strategy=retrieval_strategy,
            retrieval_k=retrieval_k,
            overlap_distance_m=overlap_distance_m,
            max_angle_deg=max_angle_deg,
            image_pairs=image_pairs,
            pairs_blob_sha=pairs_blob_sha,
            pairs_blob_format=pairs_blob_format,
            backend_options=backend_options,
            input_artifacts=input_artifacts,
        )

        pairs_spec.additional_properties = d
        return pairs_spec

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
