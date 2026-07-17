"""Shared identifier vocabularies and public-value validation helpers.

A manifest is the contract a backend ships; malformed values here are
invisible until install / discovery time, so validate the shapes up front.
Provider-id pattern lives in sfmapi.server.core.ids (single source); the other
patterns are sfm_hub-specific and stay here.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import unquote_plus, urlsplit

from pydantic import Field

_ENTRY_POINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_PUBLIC_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?$")
_PUBLIC_IMAGE_REF_RE = re.compile(
    r"^(?P<registry>[a-z0-9](?:[a-z0-9.-]*[a-z0-9])(?::[0-9]{1,5})?)/"
    r"(?P<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    r"(?:@sha256:[0-9a-fA-F]{64})?$"
)
_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CONTRACT_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_LOCAL_DECLARATION_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SPECIAL_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_ATTRIBUTE_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s,]+")
_SENSITIVE_PUBLIC_RE = re.compile(
    r"(token|secret|password|authorization|bearer|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret|private[_-]?key|credential|signature|x-amz|"
    r"x-goog-signature|awsaccesskeyid|googleaccessid|sigv4|^sig$)",
    re.IGNORECASE,
)
_RESOLVER_ENV_KEYS = {
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_FIND_LINKS",
    "PIP_NO_INDEX",
    "PIP_CONFIG_FILE",
    "UV_INDEX",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS",
    "UV_NO_INDEX",
    "UV_INDEX_STRATEGY",
    "UV_KEYRING_PROVIDER",
    "UV_CONFIG_FILE",
    "UV_NO_CONFIG",
    "UV_NO_SYNC",
}
PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH = 64
PROVIDER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
CapabilityId = Annotated[str, Field(pattern=_CONTRACT_ID_RE.pattern)]


def _decoded_path_params(path: str) -> list[str]:
    from urllib.parse import unquote

    variants = [path]
    current = path
    for _ in range(2):
        decoded = unquote(current)
        if decoded == current:
            break
        if decoded not in variants:
            variants.append(decoded)
        current = decoded
    parts: list[str] = []
    for variant in variants:
        for delimiter in (";", "?", "#"):
            if delimiter not in variant:
                continue
            for part in re.split(r"[&;#]", variant.split(delimiter, 1)[1]):
                if part and part not in parts:
                    parts.append(part)
    return parts


def _public_url_issue(value: str, *, allowed_schemes: set[str]) -> str | None:
    if not value or value.strip() != value or any(char.isspace() for char in value):
        return "must not contain whitespace"
    parsed = urlsplit(value)
    if parsed.scheme not in allowed_schemes:
        return f"must be a {'/'.join(sorted(allowed_schemes))} URL"
    if not parsed.netloc:
        return "must include a host"
    if parsed.username or parsed.password:
        return "must not include credentials"
    if parsed.query or parsed.fragment:
        return "must not include query strings or fragments"
    for part in _decoded_path_params(parsed.path):
        key, _sep, item = part.partition("=")
        if _SENSITIVE_PUBLIC_RE.search(key) or _SENSITIVE_PUBLIC_RE.search(item):
            return "must not include signed path parameters"
    return None


def _validate_public_service_path(value: str, *, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must be non-empty")
    if not value.startswith("/"):
        raise ValueError(f"{label} must start with /")
    if any(char.isspace() for char in value):
        raise ValueError(f"{label} must not contain whitespace")
    if "?" in value or "#" in value:
        raise ValueError(f"{label} must not include query strings or fragments")
    if "://" in value or "@" in value:
        raise ValueError(f"{label} must be a path, not a URL or authority")
    for part in _decoded_path_params(value):
        key, _sep, item = part.partition("=")
        if _SENSITIVE_PUBLIC_RE.search(key) or _SENSITIVE_PUBLIC_RE.search(item):
            raise ValueError(f"{label} must not include signed path parameters")
    return value


def _validate_public_https_url(value: str, *, label: str) -> str:
    issue = _public_url_issue(value, allowed_schemes={"https"})
    if issue is not None:
        raise ValueError(f"{label} {issue}")
    return value


def _validate_github_url(value: str, *, label: str) -> str:
    _validate_public_https_url(value, label=label)
    parsed = urlsplit(value)
    if parsed.netloc.lower() != "github.com":
        raise ValueError(f"{label} must be a https://github.com/<owner>/<repo> URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError(f"{label} must identify a GitHub repository")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not _GITHUB_NAME_RE.match(owner) or not _GITHUB_NAME_RE.match(repo):
        raise ValueError(f"{label} must include a valid GitHub owner and repository")
    return value


def _validate_public_ref(value: str, *, label: str) -> str:
    if (
        not _PUBLIC_REF_RE.match(value)
        or ".." in value.split("/")
        or _SENSITIVE_PUBLIC_RE.search(value)
    ):
        raise ValueError(f"{label} must be a public branch, tag, or commit")
    return value


def _validate_public_package_name(value: str, *, label: str) -> str:
    if not _PUBLIC_PACKAGE_RE.match(value) or _SENSITIVE_PUBLIC_RE.search(value):
        raise ValueError(f"{label} must be a public package name")
    return value


def _looks_like_local_path(value: str) -> bool:
    if not value:
        return False
    lower = value.lower()
    return (
        lower.startswith("file://")
        or value.startswith(("/", "\\"))
        or "\\" in value
        or (len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in ("/", "\\"))
    )


def _validate_public_relative_path(value: str, *, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must be non-empty")
    for variant in _public_text_variants(value):
        if (
            variant.strip() != variant
            or any(char.isspace() for char in variant)
            or variant.startswith(("/", "\\"))
            or "\\" in variant
            or "://" in variant
            or "@" in variant
            or "?" in variant
            or "#" in variant
            or _SENSITIVE_PUBLIC_RE.search(variant)
            or _looks_like_local_path(variant)
        ):
            raise ValueError(f"{label} must be a public relative path")
        parts = [part for part in variant.split("/") if part]
        if any(part == ".." for part in parts):
            raise ValueError(f"{label} must stay inside the build context")
    return value


def _private_registry_host(host: str) -> bool:
    normalized = host.split(":", 1)[0].rstrip(".").lower()
    if not normalized or "." not in normalized:
        return True
    if normalized in {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
    }:
        return True
    if (
        normalized.endswith(".localhost")
        or normalized.endswith(".local")
        or normalized.endswith(".internal")
    ):
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_public_image_ref(value: str | None, *, label: str) -> str | None:
    if value is None:
        return value
    for variant in _public_text_variants(value):
        if (
            variant.strip() != variant
            or any(char.isspace() for char in variant)
            or "://" in variant
            or "\\" in variant
            or "?" in variant
            or "#" in variant
            or _looks_like_local_path(variant)
            or _SENSITIVE_PUBLIC_RE.search(variant)
        ):
            raise ValueError(f"{label} must be a public container image reference")
    match = _PUBLIC_IMAGE_REF_RE.match(value)
    if match is None or _private_registry_host(match.group("registry")):
        raise ValueError(f"{label} must use an explicit public registry image reference")
    return value


def _public_text_variants(text: str) -> list[str]:
    variants = [text]
    current = text
    for _ in range(2):
        if len(current) > 4096:
            break
        decoded = unquote_plus(current)
        if decoded == current:
            break
        if decoded not in variants:
            variants.append(decoded)
        current = decoded
    return variants


def _public_text_values(value: Any) -> list[str]:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return [str(value or "")]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_public_text_values(item))
        return out
    if isinstance(value, dict):
        out = []
        for item_key, item_value in value.items():
            out.append(str(item_key))
            out.extend(_public_text_values(item_value))
        return out
    raise ValueError("public extension values must be scalar, list, or object")


def _validate_public_env_mapping(args: dict[str, str], *, label: str) -> dict[str, str]:
    for key, value in args.items():
        if not _ENV_VAR_RE.match(key):
            raise ValueError(f"{label} names must be environment-style names")
        if key in _RESOLVER_ENV_KEYS or _SENSITIVE_PUBLIC_RE.search(key):
            raise ValueError(f"{label} must not contain secrets or resolver overrides")
        for variant in _public_text_variants(value):
            if _SENSITIVE_PUBLIC_RE.search(variant) or _looks_like_local_path(variant):
                raise ValueError(f"{label} must not contain secrets or local paths")
            for url in _URL_RE.findall(variant):
                issue = _public_url_issue(url, allowed_schemes={"http", "https"})
                if issue is not None:
                    raise ValueError(f"{label} URLs {issue}")
    return args


def _validate_public_build_args(args: dict[str, str]) -> dict[str, str]:
    return _validate_public_env_mapping(args, label="container service build args")


def _provider_id_re() -> re.Pattern[str]:
    """Late import: ``sfmapi.server.core.ids`` ships only stdlib but
    ``sfmapi.server`` and ``sfm_hub`` cross-import elsewhere, so resolve
    lazily to avoid Python's module-import-cycle serialization."""
    from sfmapi.server.core.ids import PROVIDER_ID_RE

    return PROVIDER_ID_RE


def _known_capabilities() -> frozenset[str]:
    """The canonical capability vocabulary, imported lazily.

    Late import: ``sfmapi.server.core.capabilities`` depends only on stdlib and never
    imports ``sfm_hub``, so this adds no import cycle — but keeping it inside
    the function avoids a module-load-time edge from the lower-level hub
    package up into ``app``.
    """
    from sfmapi.server.core.capabilities import ALL_KNOWN

    return ALL_KNOWN


def _core_datatype_ids() -> frozenset[str]:
    from sfmapi.server.core.datatypes import CORE_DATA_TYPES_BY_ID

    return frozenset(CORE_DATA_TYPES_BY_ID)


def _core_processor_ids() -> frozenset[str]:
    from sfmapi.server.core.processors import PROCESSORS_BY_ID

    return frozenset(PROCESSORS_BY_ID)


def _core_pipeline_ids() -> frozenset[str]:
    from sfmapi.server.core.pipelines import CANONICAL_PIPELINES

    return frozenset(CANONICAL_PIPELINES)


def _deny_core_ids_schema(
    ids_fn: Callable[[], frozenset[str]],
) -> Callable[[dict[str, Any]], None]:
    def _apply(schema: dict[str, Any]) -> None:
        try:
            forbidden = sorted(ids_fn())
        except Exception:  # pragma: no cover - schema generation diagnostic only
            return
        schema["not"] = {"enum": forbidden}

    return _apply
