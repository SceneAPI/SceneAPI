"""Filesystem path validation for user-supplied relative paths."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from app.core.errors import ValidationError


def validate_safe_relative_path(value: str, *, field: str = "path") -> str:
    """Return ``value`` if it is a portable relative path inside a root.

    Local datasets intentionally accept image names with subdirectories. They
    must not accept absolute paths, drive-qualified paths, or parent traversal
    because the same values are later used as source paths and stage outputs.
    """
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty relative path")
    if "\x00" in value:
        raise ValidationError(f"{field} must not contain NUL bytes")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        raise ValidationError(f"{field} must be a relative path")
    normalized = value.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValidationError(f"{field} must stay under the configured root")
    return value


def resolve_under_root(
    root: str | Path,
    rel_path: str,
    *,
    field: str = "path",
    require_file: bool = False,
) -> Path:
    """Resolve ``rel_path`` below ``root`` and reject root escapes."""
    validate_safe_relative_path(rel_path, field=field)
    try:
        root_path = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ValidationError(f"{field} root does not exist") from exc
    target = root_path / rel_path
    if require_file:
        if not target.is_file():
            raise ValidationError(f"{field} does not reference an existing file")
        target_resolved = target.resolve(strict=True)
    else:
        target_resolved = target.resolve(strict=False)
    try:
        target_resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValidationError(f"{field} must stay under the configured root") from exc
    return target_resolved
