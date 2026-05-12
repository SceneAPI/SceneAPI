"""Local diagnostic checks for plugin registry entries."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sfm_hub.models import PluginManifest
from sfm_hub.state import PluginState, load_state


class DoctorCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    status: Literal["pass", "warn", "fail"]
    checks: list[DoctorCheck] = Field(default_factory=list)


class ToolDetection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: Literal["env", "path"]
    path: str | None = None
    version: str | None = None
    error: str | None = None


def _version_for(path: str, args: list[str]) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            [path, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # pragma: no cover - defensive around local tools
        return None, f"{type(exc).__name__}: {exc}"
    text = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return text[:500] or None, f"exit {result.returncode}"
    return text[:500] or None, None


def detect_external_tools(manifests: list[PluginManifest]) -> dict[str, list[ToolDetection]]:
    out: dict[str, list[ToolDetection]] = {}
    for manifest in manifests:
        external = manifest.runtime_modes.external_tool
        if external is None:
            continue
        detections: list[ToolDetection] = []
        seen_paths: set[str] = set()
        for env_var in external.env_vars:
            value = os.environ.get(env_var)
            if not value:
                detections.append(ToolDetection(name=env_var, source="env"))
                continue
            version, error = _version_for(value, external.version_args)
            seen_paths.add(value)
            detections.append(
                ToolDetection(
                    name=env_var,
                    source="env",
                    path=value,
                    version=version,
                    error=error,
                )
            )
        for executable in external.executable_names:
            path = shutil.which(executable)
            if path is None:
                detections.append(ToolDetection(name=executable, source="path"))
                continue
            if path in seen_paths:
                continue
            version, error = _version_for(path, external.version_args)
            detections.append(
                ToolDetection(
                    name=executable,
                    source="path",
                    path=path,
                    version=version,
                    error=error,
                )
            )
        out[manifest.plugin_id] = detections
    return out


def doctor_manifest(manifest: PluginManifest, *, state: PluginState | None = None) -> DoctorReport:
    state = state or load_state()
    checks: list[DoctorCheck] = []
    installed = state.installed.get(manifest.plugin_id)
    checks.append(
        DoctorCheck(
            name="manifest",
            status="pass",
            detail=f"{manifest.plugin_id} manifest is valid",
        )
    )
    checks.append(
        DoctorCheck(
            name="installation",
            status="pass" if installed is not None else "warn",
            detail="plugin is recorded as installed" if installed else "plugin is not installed",
        )
    )
    if installed is not None:
        checks.append(
            DoctorCheck(
                name="enabled",
                status="pass" if installed.enabled else "warn",
                detail="plugin is enabled" if installed.enabled else "plugin is disabled",
            )
        )
    external = manifest.runtime_modes.external_tool
    if external is not None:
        found = detect_external_tools([manifest]).get(manifest.plugin_id, [])
        usable = [item for item in found if item.path and not item.error]
        found_paths = [item for item in found if item.path]
        checks.append(
            DoctorCheck(
                name="external_tool",
                status="pass" if usable else "warn",
                detail="; ".join(
                    f"{item.name}={item.path or 'missing'}"
                    + (f" version={item.version}" if item.version else "")
                    + (f" error={item.error}" if item.error else "")
                    for item in found_paths or found
                ),
            )
        )
    if any(check.status == "fail" for check in checks):
        status: Literal["pass", "warn", "fail"] = "fail"
    elif any(check.status == "warn" for check in checks):
        status = "warn"
    else:
        status = "pass"
    return DoctorReport(plugin_id=manifest.plugin_id, status=status, checks=checks)
