"""Shared descriptor-registry core for the ``backend_*`` adapter triplet.

``backend_config``, ``backend_actions``, and ``backend_artifacts`` each
expose the same registry shape over one backend extension surface: a
normalized descriptor listing (``list_backend_*``), a cheap probe
(``has_backend_*``), a single-descriptor read (``get_backend_*``), and a
contract validator (``*_contract_violations`` / ``assert_*_contract``).
This module owns the machinery those surfaces share; each module
parameterizes one :class:`DescriptorRegistry` with its descriptor id key,
wire paths, and violation wording.

Internal to ``sceneapi.server.adapters`` -- services and plugins keep
importing the three public modules. Every string produced here feeds
pinned ``/v1/backend/*`` wire responses, MCP tools, or plugin contract
checks, so helpers must stay byte-stable.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import quote

from sceneapi.server.core.capabilities import ALL_KNOWN
from sceneapi.server.core.errors import NotFoundError, ValidationError
from sceneapi.server.core.ids import NAMESPACED_ID_RE, PROVIDER_ID_RE


def backend_name(backend: Any) -> str:
    """The backend's advertised name (``"unknown"`` when absent)."""
    return str(getattr(backend, "name", "unknown"))


def call_with_supported_kwargs(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` with only the optional kwargs its signature accepts."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return fn(*args, **kwargs)
    supported = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return fn(*args, **supported)


def optional_str(value: Any) -> str | None:
    """``None`` passthrough, everything else coerced to ``str``."""
    return None if value is None else str(value)


def descriptor_display_name(raw: Mapping[str, Any], default: str) -> Any:
    """Falsy-chained ``display_name`` -> ``title`` -> id fallback used by the
    config-schema and artifact-contract normalizers. (Action descriptors
    distinguish key *presence* and keep their own logic.)
    """
    return raw.get("display_name") or raw.get("title") or default


def probe_listing(list_rows: Callable[[], Sequence[Any]]) -> bool:
    """``has_backend_*`` semantics: non-empty listing; any failure -> False."""
    try:
        return bool(list_rows())
    except Exception:
        return False


def provider_violation(label: str, provider: Any) -> str | None:
    """Provider-id pattern check (wording shared by config + artifacts)."""
    if provider is None or PROVIDER_ID_RE.match(str(provider)):
        return None
    return f"{label}: provider must match /^[A-Za-z0-9][A-Za-z0-9_.-]*$/"


def capability_violation(label: str, capability: Any) -> str | None:
    """Capability-portability check (wording shared by config + artifacts)."""
    if capability is None or str(capability) in ALL_KNOWN:
        return None
    return f"{label}: capability {capability!r} is not portable"


@dataclass(frozen=True)
class DescriptorRegistry:
    """Per-module parameterization of the shared registry/validator core."""

    # Descriptor id field: "config_id" / "action_id" / "contract_id".
    id_key: str
    # Noun in the missing-id ValidationError, e.g. "config schema".
    descriptor_noun: str
    # NotFoundError prefix, e.g. "Backend config schema".
    title: str
    # First line of the ``assert_*_contract`` AssertionError.
    violation_heading: str
    # Wire collection path, e.g. "/v1/backend/config-schemas".
    collection_path: str
    # Violation label for rows without an id: f"{index_label}[{index}]".
    index_label: str
    # Backend provider method name == public list function name (failure msgs).
    list_method: str
    # Extra ``:op`` links (actions: ("validate", "run")).
    link_operations: tuple[str, ...] = ()
    # Whether ``_links`` carries a ``collection`` entry.
    link_collection: bool = True
    # Stage sort weights for dedupe; ``None`` sorts by id only.
    stage_order: Mapping[str, int] | None = None

    def links(self, item_id: str) -> dict[str, dict[str, str]]:
        """The ``_links`` object for one descriptor (key order is wire-pinned)."""
        encoded = quote(item_id, safe="")
        links = {"self": {"href": f"{self.collection_path}/{encoded}"}}
        if self.link_collection:
            links["collection"] = {"href": self.collection_path}
        for operation in self.link_operations:
            links[operation] = {"href": f"{self.collection_path}/{encoded}:{operation}"}
        return links

    def descriptor_id(self, raw: Mapping[str, Any]) -> str:
        """Resolve ``id_key``/``id``/``name`` or raise the pinned ValidationError."""
        item_id = str(raw.get(self.id_key) or raw.get("id") or raw.get("name") or "").strip()
        if not item_id:
            raise ValidationError(
                f"backend {self.descriptor_noun} descriptor missing {self.id_key}"
            )
        return item_id

    def dedupe(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """First-wins de-duplication by id, sorted by (stage order, id)."""
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            by_id.setdefault(str(row[self.id_key]), row)
        if self.stage_order is None:
            return [by_id[key] for key in sorted(by_id)]
        order = self.stage_order
        return sorted(
            by_id.values(),
            key=lambda item: (order.get(str(item.get("stage")), 999), str(item[self.id_key])),
        )

    def list_rows(
        self,
        backend: Any,
        *,
        normalize: Callable[[Any], dict[str, Any]],
        fallback: Callable[[], list[dict[str, Any]]],
        call_kwargs: dict[str, Any] | None = None,
        generic_wins: bool = True,
    ) -> list[dict[str, Any]]:
        """Provider-first listing shared by the three ``list_backend_*``.

        Rows from the backend's ``list_method`` are normalized; with
        ``generic_wins`` a non-empty provider listing short-circuits
        ``fallback`` (config, artifacts), otherwise provider rows and
        fallback rows are merged first-wins (actions). ``call_kwargs``
        are signature-filtered; ``None`` calls the provider bare.
        """
        rows: list[dict[str, Any]] = []
        generic = getattr(backend, self.list_method, None)
        if callable(generic):
            raws = (
                generic()
                if call_kwargs is None
                else call_with_supported_kwargs(generic, **call_kwargs)
            )
            rows = [normalize(raw) for raw in raws]
            if rows and generic_wins:
                return self.dedupe(rows)
        rows.extend(fallback())
        return self.dedupe(rows)

    def find(self, rows: Iterable[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
        for row in rows:
            if row[self.id_key] == item_id:
                return row
        return None

    def raise_not_found(self, item_id: str) -> NoReturn:
        raise NotFoundError(f"{self.title} {item_id!r} not found")

    def get_row(self, rows: Iterable[dict[str, Any]], item_id: str) -> dict[str, Any]:
        """``get_backend_*`` core: first row matching ``item_id`` or NotFound."""
        row = self.find(rows, item_id)
        if row is None:
            self.raise_not_found(item_id)
        return row

    def listing_failed(self, exc: Exception) -> list[str]:
        """The whole-listing failure violation for ``*_contract_violations``."""
        return [f"{self.list_method}() failed: {exc}"]

    def row_label(self, item_id: str, index: int) -> str:
        """Violation label: the id, or a positional stand-in when missing."""
        return item_id or f"{self.index_label}[{index}]"

    def missing_id_violation(self, label: str) -> str:
        return f"{label}: {self.id_key} is required"

    def namespaced_id_violation(self, label: str, item_id: str) -> str | None:
        """``NAMESPACED_ID_RE`` check with the config/artifacts wording.

        (Actions keep their looser ``"." in action_id`` check, with a
        different example, local to ``backend_actions``.)
        """
        if NAMESPACED_ID_RE.match(item_id):
            return None
        return f"{label}: {self.id_key} should be namespaced, e.g. vendor.stage"

    def duplicate_violations(self, ids: Sequence[str]) -> list[str]:
        duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        return [f"{item_id}: duplicate {self.id_key}" for item_id in duplicates]

    def assert_contract(self, violations: list[str]) -> None:
        """Raise the pinned ``AssertionError`` when violations exist."""
        if violations:
            raise AssertionError(
                f"{self.violation_heading}\n"
                + "\n".join(f"- {violation}" for violation in violations)
            )
