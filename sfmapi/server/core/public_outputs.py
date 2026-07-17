"""Public sanitization of task ``outputs_ref`` -- strip host filesystem paths.

The worker writes raw result payloads to ``outputs_ref_json``, often carrying
host filesystem paths (a sealed snapshot dir, artifact uris, workspace mounts).
Those must not leak to API clients. This sanitizer is applied when a task is
serialized for the wire, and mirrors the C++ port's ``sanitize_public_json``
BYTE-FOR-BYTE so the served job shape is identical across tiers:

* ``host_path`` / ``workspace`` keys are dropped;
* a plugin's ``url`` is dropped when ``plugin_id`` + ``provider`` siblings exist;
* a local-path file-ref ``uri`` / ``path`` is omitted and its basename is lifted
  to ``name`` (if absent);
* any other ``*path*`` / ``*file*`` key with a local-path value -> its basename;
* non-local uris (``http://``, ``memory://``, ``s3://`` ...) pass through.
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, unquote_plus, urlparse

_PUBLIC_FILE_REF_KEYS = ("media_type", "sha256", "byte_size")
_PUBLIC_API_PATH_RE = re.compile(r"^/v\d+(?:/|$)")
_PUBLIC_URL_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"')>,]+",
    re.IGNORECASE,
)
_PUBLIC_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/][^\s\"')>,]+")
_PUBLIC_POSIX_PATH_RE = re.compile(r"(?<![\w.:/])/(?!v\d+(?:/|$)|/)[^\s\"')>,;]*")
_PUBLIC_BACKSLASH_PATH_RE = re.compile(r"(?<![\w.:/\\])\\[^\s\"')>,;]*")
_PUBLIC_DROP_KEY_RE = re.compile(
    r"(host_?path|local_?path|sealed_?path|workspace|mount|env|secret|token|password|"
    r"authorization|cookie|api[_-]?key|access[_-]?key|accesskey|credential|credentials|"
    r"bearer|private[_-]?key|client[_-]?secret|x-amz|x-goog-signature|"
    r"awsaccesskeyid|googleaccessid|signature|sigv4|^sig$|response_body|body)",
    re.IGNORECASE,
)
_PUBLIC_SENSITIVE_KEY_RE = re.compile(
    r"(url|uri|path|root|key|secret|credential|authorization|bearer)",
    re.IGNORECASE,
)
_PUBLIC_PRIVATE_MARKERS = (
    "SFMAPI_",
    "_container_services",
    "_bridge_backend_actions",
    "host_path",
    "sealed_path",
)
_PUBLIC_SECRET_TEXT_RE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|AUTHORIZATION|BEARER|API[_-]?KEY|ACCESS[_-]?KEY|"
    r"CLIENT[_-]?SECRET|PRIVATE[_-]?KEY|CREDENTIAL|SFMAPI_)",
    re.IGNORECASE,
)
_REMOTE_URI_SENSITIVE_QUERY_RE = re.compile(
    r"(token|secret|password|signature|credential|credentials|authorization|"
    r"access[_-]?key|accesskey|api[_-]?key|apikey|private[_-]?key|"
    r"client[_-]?secret|x-amz|x-goog-signature|awsaccesskeyid|"
    r"googleaccessid|sigv4|sig|bearer|^key$)",
    re.IGNORECASE,
)
_SIGNED_PARAM_TEXT_RE = re.compile(
    r"(^|[?&#;,\s])"
    r"(x-amz-[A-Za-z0-9_-]*|x-goog-signature|awsaccesskeyid|googleaccessid|"
    r"signature|sigv4|sig)"
    r"\s*[:=]",
    re.IGNORECASE,
)
_LEGACY_IPV4_LABEL_RE = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)")


def _contains_public_private_marker(text: str) -> bool:
    lower = text.lower()
    return any(marker.lower() in lower for marker in _PUBLIC_PRIVATE_MARKERS)


def _legacy_numeric_ipv4_host(normalized: str) -> bool:
    labels = normalized.split(".")
    return len(labels) > 1 and all(_LEGACY_IPV4_LABEL_RE.fullmatch(label) for label in labels)


def _remote_query_pairs(query: str) -> list[tuple[str, str]]:
    """Query pairs using both RFC3986 '&' and legacy ';' separators."""
    pairs: list[tuple[str, str]] = []
    for part in re.split(r"[&;]", query):
        if not part:
            continue
        key, sep, value = part.partition("=")
        pairs.append((unquote_plus(key), unquote_plus(value if sep else "")))
    return pairs


def _remote_path_param_pairs(path: str) -> list[tuple[str, str]]:
    variants = [path]
    current = path
    for _ in range(2):
        decoded = unquote(current)
        if decoded == current:
            break
        if decoded not in variants:
            variants.append(decoded)
        current = decoded
    pairs: list[tuple[str, str]] = []
    for variant in variants:
        for delimiter in (";", "?", "#"):
            if delimiter not in variant:
                continue
            for part in re.split(r"[&;#]", variant.split(delimiter, 1)[1]):
                if not part:
                    continue
                key, sep, value = part.partition("=")
                pair = (unquote_plus(key), unquote_plus(value if sep else ""))
                if pair not in pairs:
                    pairs.append(pair)
    return pairs


def _public_remote_pair_is_sensitive(key: str, item: str) -> bool:
    decoded_item = unquote_plus(item)
    return (
        _REMOTE_URI_SENSITIVE_QUERY_RE.search(key)
        or _REMOTE_URI_SENSITIVE_QUERY_RE.search(item)
        or _REMOTE_URI_SENSITIVE_QUERY_RE.search(decoded_item)
        or _PUBLIC_SECRET_TEXT_RE.search(key)
        or _PUBLIC_SECRET_TEXT_RE.search(item)
        or _PUBLIC_SECRET_TEXT_RE.search(decoded_item)
        or _contains_public_private_marker(key)
        or _contains_public_private_marker(item)
        or _contains_public_private_marker(decoded_item)
    )


def _has_url_scheme(s: str) -> bool:
    i = s.find("://")
    if i <= 0:  # not found, or "://" at position 0 -> no scheme
        return False
    return all(c.isalnum() or c in "+-." for c in s[:i])


def _is_local_uri(s: str) -> bool:
    if not s:
        return False
    lower = s.lower()
    if _PUBLIC_API_PATH_RE.match(s):
        return False
    if lower.startswith("file://"):
        return True
    if s[0] in ("/", "\\"):
        return True
    if len(s) >= 3 and s[0].isalpha() and s[1] == ":" and s[2] in ("\\", "/"):
        return True  # drive-letter path, e.g. C:\ or C:/
    return not _has_url_scheme(s)


def _is_local_artifact_reference(value: str) -> bool:
    if not value:
        return False
    lower = value.lower()
    if _PUBLIC_API_PATH_RE.match(value):
        return False
    if lower.startswith("file://"):
        return True
    if "\\" in value:
        return True
    if value[0] == "/":
        return True
    if len(value) >= 2 and value[0].isalpha() and value[1] == ":":
        return True
    if _has_url_scheme(value):
        return False
    return ".." in value.split("/")


def _base_name(p: str) -> str:
    i = max(p.rfind("/"), p.rfind("\\"))
    return p if i == -1 else p[i + 1 :]


def _local_reference_path(value: str) -> Path | None:
    if not value or _PUBLIC_API_PATH_RE.match(value):
        return None
    if value.lower().startswith("file://"):
        parsed = urlparse(value)
        raw_path = unquote(parsed.path)
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        if (
            len(raw_path) >= 3
            and raw_path[0] == "/"
            and raw_path[1].isalpha()
            and raw_path[2] == ":"
        ):
            raw_path = raw_path[1:]
        return Path(raw_path)
    if _has_url_scheme(value):
        return None
    if value[0] in ("/", "\\") or "\\" in value:
        return Path(value)
    if len(value) >= 2 and value[0].isalpha() and value[1] == ":":
        return Path(value)
    return None


def _same_local_reference(value: str, public_content_path: str) -> bool:
    candidate = _local_reference_path(value)
    if candidate is None or not public_content_path:
        return False
    try:
        left = os.path.normcase(os.path.abspath(os.fspath(candidate)))
        right = os.path.normcase(os.path.abspath(public_content_path))
    except OSError:
        return False
    return left == right


def sanitize_public_artifact_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value
    if _is_local_artifact_reference(text) or _is_local_uri(text):
        text = _base_name(text)
    text = _redact_public_text(text, key="name")
    return text[:255] if text else None


def _private_network_artifact_host(parsed: Any) -> bool:
    scheme = parsed.scheme.lower()
    if scheme == "http":
        return True
    if scheme != "https":
        return False
    host = parsed.hostname
    if not host:
        return True
    normalized = host.rstrip(".").lower()
    if not normalized.isascii():
        return True
    if normalized in {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
    }:
        return True
    if "%" in normalized:
        return True
    if (
        normalized.endswith(".localhost")
        or normalized.endswith(".local")
        or normalized.endswith(".internal")
        or "." not in normalized
    ):
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return bool(re.fullmatch(r"[0-9.]+", normalized) or _legacy_numeric_ipv4_host(normalized))
    shared_carrier_nat = ip.version == 4 and ipaddress.IPv4Address(
        "100.64.0.0"
    ) <= ip <= ipaddress.IPv4Address("100.127.255.255")
    return (
        shared_carrier_nat
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def sanitize_public_artifact_uri(value: Any) -> str | None:
    """Return a remote/public artifact URI only when it carries no credentials."""
    if not isinstance(value, str) or not value:
        return None
    if _is_local_artifact_reference(value) or _is_local_uri(value):
        return None
    if _PUBLIC_SECRET_TEXT_RE.search(value) or _contains_public_private_marker(value):
        return None
    parsed = urlparse(value)
    if parsed.scheme.lower() == "file":
        return None
    if _private_network_artifact_host(parsed):
        return None
    path_with_params = parsed.path + (f";{parsed.params}" if parsed.params else "")
    decoded_path = unquote(path_with_params)
    if _PUBLIC_SECRET_TEXT_RE.search(decoded_path) or _contains_public_private_marker(decoded_path):
        return None
    for key, item in _remote_path_param_pairs(path_with_params):
        if _public_remote_pair_is_sensitive(key, item):
            return None
    if parsed.username or parsed.password:
        return None
    if parsed.query:
        for key, item in _remote_query_pairs(parsed.query):
            if _public_remote_pair_is_sensitive(key, item):
                return None
    if parsed.fragment:
        for key, item in _remote_query_pairs(parsed.fragment):
            if _public_remote_pair_is_sensitive(key, item):
                return None
    return value[:2048]


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


def _remote_uri_text_is_sensitive(text: str) -> bool:
    for url in _PUBLIC_URL_RE.findall(text):
        parsed = urlparse(url)
        path_with_params = parsed.path + (f";{parsed.params}" if parsed.params else "")
        decoded_path = unquote(path_with_params)
        if (
            parsed.username
            or parsed.password
            or _PUBLIC_SECRET_TEXT_RE.search(decoded_path)
            or _contains_public_private_marker(decoded_path)
        ):
            return True
        for key, item in _remote_path_param_pairs(path_with_params):
            if _public_remote_pair_is_sensitive(key, item):
                return True
        for part in (parsed.query, parsed.fragment):
            if not part:
                continue
            for key, item in _remote_query_pairs(part):
                if _public_remote_pair_is_sensitive(key, item):
                    return True
    return False


def _signed_param_text_is_sensitive(text: str) -> bool:
    return bool(_SIGNED_PARAM_TEXT_RE.search(text))


def _redact_public_text(value: Any, *, key: str | None = None) -> str:
    text = str(value or "")
    key_sensitive = bool(key and _PUBLIC_SENSITIVE_KEY_RE.search(key))
    variants = _public_text_variants(text)
    raw_had_url_or_path = bool(
        _PUBLIC_URL_RE.search(text)
        or _PUBLIC_WIN_PATH_RE.search(text)
        or _PUBLIC_POSIX_PATH_RE.search(text)
        or _PUBLIC_BACKSLASH_PATH_RE.search(text)
    )
    had_url_or_path = bool(
        any(
            _PUBLIC_URL_RE.search(variant)
            or _PUBLIC_WIN_PATH_RE.search(variant)
            or _PUBLIC_POSIX_PATH_RE.search(variant)
            or _PUBLIC_BACKSLASH_PATH_RE.search(variant)
            for variant in variants
        )
    )
    hard_private = bool(
        any(
            _PUBLIC_SECRET_TEXT_RE.search(variant)
            or _contains_public_private_marker(variant)
            or _signed_param_text_is_sensitive(variant)
            or (_remote_uri_text_is_sensitive(variant) and not raw_had_url_or_path)
            for variant in variants
        )
    )
    text = _PUBLIC_URL_RE.sub("<redacted>", text)
    text = _PUBLIC_WIN_PATH_RE.sub("<redacted>", text)
    text = _PUBLIC_POSIX_PATH_RE.sub("<redacted>", text)
    text = _PUBLIC_BACKSLASH_PATH_RE.sub("<redacted>", text)
    if (
        hard_private
        or (had_url_or_path and not raw_had_url_or_path)
        or (key_sensitive and had_url_or_path)
    ):
        return "<redacted>"
    return text[:2000]


def _sanitize_public_file_ref(value: Any) -> Any:
    if not isinstance(value, dict):
        return _sanitize_public_outputs(value)
    out: dict[str, Any] = {}
    name = value.get("name")
    path = value.get("path")
    if isinstance(name, str):
        public_name = sanitize_public_artifact_name(name)
        if public_name:
            out["name"] = public_name
    elif isinstance(path, str) and _is_local_uri(path):
        public_name = sanitize_public_artifact_name(path)
        if public_name:
            out["name"] = public_name

    uri = value.get("uri")
    if isinstance(uri, str):
        if _is_local_uri(uri):
            if not isinstance(out.get("name"), str):
                public_name = sanitize_public_artifact_name(uri)
                if public_name:
                    out["name"] = public_name
        else:
            public_uri = sanitize_public_artifact_uri(uri)
            if public_uri:
                out["uri"] = public_uri

    for key in _PUBLIC_FILE_REF_KEYS:
        if key in value:
            out[key] = _sanitize_public_outputs(value[key])
    return out


def sanitize_public_artifact_file_refs(
    files: Any,
    *,
    public_content_href: str = "",
    public_content_path: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(files, list):
        return []
    public: list[dict[str, Any]] = []
    for file_ref in files:
        if not isinstance(file_ref, dict):
            continue
        name = sanitize_public_artifact_name(file_ref.get("name"))
        uri = file_ref.get("uri")
        path = file_ref.get("path")
        public_uri: str | None = None
        if isinstance(uri, str):
            if _is_local_artifact_reference(uri):
                public_uri = (
                    public_content_href if _same_local_reference(uri, public_content_path) else None
                )
            else:
                public_uri = sanitize_public_artifact_uri(uri)
        elif isinstance(path, str) and _is_local_artifact_reference(path):
            public_uri = (
                public_content_href if _same_local_reference(path, public_content_path) else None
            )
        if not name:
            for candidate in (path, uri):
                if isinstance(candidate, str):
                    name = sanitize_public_artifact_name(candidate)
                    if name:
                        break
        if not public_uri:
            continue
        item: dict[str, Any] = {"name": name or "artifact", "uri": public_uri}
        for key in _PUBLIC_FILE_REF_KEYS:
            if key in file_ref:
                item[key] = _sanitize_public_outputs(file_ref[key])
        public.append(item)
    return public


def _sanitize_public_outputs(
    value: Any,
    *,
    _artifact_descriptor: bool = False,
    _file_ref: bool = False,
) -> Any:
    if isinstance(value, list):
        return [
            _sanitize_public_outputs(
                item,
                _artifact_descriptor=_artifact_descriptor,
                _file_ref=_file_ref,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _redact_public_text(value)
    if not isinstance(value, dict):
        return value
    if _file_ref:
        return _sanitize_public_file_ref(value)
    out: dict[str, Any] = {}
    for key, child in value.items():
        text_key = str(key)
        if _PUBLIC_DROP_KEY_RE.search(text_key) or _contains_public_private_marker(text_key):
            continue
        if key in ("host_path", "workspace"):
            continue
        if key == "url" and "plugin_id" in value and "provider" in value:
            continue
        if key in ("path", "uri") and isinstance(child, str) and _is_local_uri(child):
            if key == "path":
                if not isinstance(value.get("name"), str):
                    out["name"] = _base_name(child)
                continue
            out[key] = None
            continue
        if (
            _artifact_descriptor
            and key == "uri"
            and isinstance(child, str)
            and not _is_local_artifact_reference(child)
        ):
            public_uri = sanitize_public_artifact_uri(child)
            if public_uri:
                out[key] = public_uri
            continue
        if (
            isinstance(child, str)
            and _is_local_uri(child)
            and ("path" in text_key or "file" in text_key)
        ):
            out[key] = (
                _base_name(child)
                if _artifact_descriptor
                else _redact_public_text(child, key=text_key)
            )
            continue
        if isinstance(child, str):
            out[key] = _redact_public_text(child, key=text_key)
            continue
        if key == "artifacts" and isinstance(child, list):
            out[key] = [_sanitize_public_outputs(item, _artifact_descriptor=True) for item in child]
            continue
        if _artifact_descriptor and key == "files" and isinstance(child, list):
            out[key] = [_sanitize_public_outputs(item, _file_ref=True) for item in child]
            continue
        if _artifact_descriptor and key == "metadata" and isinstance(child, dict):
            out[key] = _sanitize_public_outputs(child, _artifact_descriptor=True)
            continue
        out[key] = _sanitize_public_outputs(child)
    return out


def sanitize_public_outputs(value: Any) -> Any:
    return _sanitize_public_outputs(value)


_DROP_PUBLIC_METADATA_VALUE = object()


def _sanitize_public_artifact_metadata_value(
    value: Any,
    *,
    key: str | None = None,
) -> Any:
    if isinstance(value, str):
        if key == "name":
            return sanitize_public_artifact_name(value)
        if _is_local_artifact_reference(value):
            return _DROP_PUBLIC_METADATA_VALUE
        return _redact_public_text(value, key=key)
    if isinstance(value, list):
        public_list = []
        for item in value:
            public_item = _sanitize_public_artifact_metadata_value(item, key=key)
            if public_item is not _DROP_PUBLIC_METADATA_VALUE:
                public_list.append(public_item)
        return public_list
    if isinstance(value, dict):
        public_dict: dict[str, Any] = {}
        for item_key, item in value.items():
            text_key = str(item_key)
            if _PUBLIC_DROP_KEY_RE.search(text_key) or _contains_public_private_marker(text_key):
                continue
            public_item = _sanitize_public_artifact_metadata_value(
                item,
                key=text_key,
            )
            if public_item is not _DROP_PUBLIC_METADATA_VALUE:
                public_dict[item_key] = public_item
        return public_dict
    return value


def sanitize_public_artifact_metadata(value: Any) -> Any:
    public_value = _sanitize_public_artifact_metadata_value(value)
    return None if public_value is _DROP_PUBLIC_METADATA_VALUE else public_value


def sanitize_public_artifact_metadata_dict(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    public_value = sanitize_public_artifact_metadata(value)
    return public_value if isinstance(public_value, dict) else {}


def sanitize_public_error_message(value: Any) -> str:
    redacted = _redact_public_text(value)
    if redacted == "<redacted>":
        return "task execution failed"
    return redacted


def sanitize_public_error(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    public = sanitize_public_outputs(value)
    out = public if isinstance(public, dict) else {}
    if "message" in value:
        out["message"] = sanitize_public_error_message(value.get("message"))
    return out


__all__ = [
    "sanitize_public_artifact_file_refs",
    "sanitize_public_artifact_metadata",
    "sanitize_public_artifact_metadata_dict",
    "sanitize_public_artifact_name",
    "sanitize_public_artifact_uri",
    "sanitize_public_error",
    "sanitize_public_error_message",
    "sanitize_public_outputs",
]
