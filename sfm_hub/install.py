"""GitHub source parsing and uv install planning for backend plugins."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

from sfm_hub.models import ContainerServiceRuntime, DockerRuntime

MUTABLE_REFS = {"main", "master", "develop", "dev", "trunk"}
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _container_service_direct_reference(
    plugin_id: str, runtime: ContainerServiceRuntime
) -> str:
    image = runtime.image
    if image is None:
        endpoint = runtime.service.default_url or f"${runtime.service.url_env}"
        return f"container_service:{endpoint}"
    if image.image:
        return f"{image.image}@{image.digest}" if image.digest else image.image
    if image.build is not None:
        source = image.build.context or image.build.source
        ref = f"@{image.build.ref}" if image.build.ref else ""
        return f"build:{source}{ref}"
    return f"container_service:{plugin_id}"


@dataclass(frozen=True)
class GitHubSource:
    url: str
    ref: str = "main"
    package: str | None = None

    @property
    def normalized_url(self) -> str:
        url = self.url.removesuffix("/")
        if not url.endswith(".git"):
            url = f"{url}.git"
        return url

    @property
    def repo_name(self) -> str:
        return self.normalized_url.removesuffix(".git").rsplit("/", 1)[-1]

    @property
    def inferred_package(self) -> str:
        return self.package or self.repo_name.replace("_", "-")


@dataclass(frozen=True)
class InstallPlan:
    method: Literal["uv", "docker", "container_service", "external_tool"]
    source: GitHubSource
    direct_reference: str
    command: list[str]
    warnings: list[str]
    resolved_commit: str | None = None


def parse_github_source(
    url: str, *, ref: str | None = None, package: str | None = None
) -> GitHubSource:
    """Normalize a GitHub URL or uv-style git reference into a source."""

    text = url.strip()
    if not text:
        raise ValueError("GitHub URL is required")
    text = text.removeprefix("git+")

    inline_ref: str | None = None
    if ("@" in text and not text.startswith("https://github.com/")) or (
        text.startswith("https://github.com/") and text.count("@") == 1
    ):
        text, inline_ref = text.rsplit("@", 1)

    tree_marker = "/tree/"
    if tree_marker in text:
        text, inline_ref = text.split(tree_marker, 1)

    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", text):
        text = f"https://github.com/{text}"
    if not text.startswith("https://github.com/"):
        raise ValueError("plugin source must be a https://github.com/... URL")

    parts = text.removesuffix("/").split("/")
    if len(parts) < 5 or not parts[-2] or not parts[-1]:
        raise ValueError("GitHub URL must include owner and repository")
    normalized = "/".join(parts[:5]).removesuffix(".git")
    chosen_ref = ref or inline_ref or "main"
    return GitHubSource(url=normalized, ref=chosen_ref, package=package)


def build_install_plan(source: GitHubSource) -> InstallPlan:
    warnings: list[str] = []
    if source.ref in MUTABLE_REFS:
        warnings.append(
            f"ref {source.ref!r} is mutable; prefer a release tag or commit SHA for verified installs"
        )
    resolved_commit = source.ref if COMMIT_RE.match(source.ref) else None
    direct_reference = f"{source.inferred_package} @ git+{source.normalized_url}@{source.ref}"
    return InstallPlan(
        method="uv",
        source=source,
        direct_reference=direct_reference,
        command=["uv", "pip", "install", direct_reference],
        warnings=warnings,
        resolved_commit=resolved_commit,
    )


def build_docker_install_plan(
    plugin_id: str,
    runtime: DockerRuntime | None,
    *,
    source: GitHubSource,
) -> InstallPlan:
    warnings: list[str] = []
    command: list[str] = []
    direct_reference = f"docker:{plugin_id}"
    if runtime is None:
        warnings.append(f"plugin {plugin_id!r} does not define a docker runtime")
    elif runtime.image:
        command = ["docker", "pull", runtime.image]
        direct_reference = runtime.image
    elif runtime.build_context:
        command = ["docker", "build", "-t", plugin_id, runtime.build_context]
        direct_reference = f"build:{runtime.build_context}"
    else:
        warnings.append(
            f"plugin {plugin_id!r} declares docker support but has no image or build_context"
        )
    return InstallPlan(
        method="docker",
        source=source,
        direct_reference=direct_reference,
        command=command,
        warnings=warnings,
    )


def build_container_service_install_plan(
    plugin_id: str,
    runtime: ContainerServiceRuntime | None,
    *,
    source: GitHubSource,
) -> InstallPlan:
    warnings: list[str] = []
    command: list[str] = []
    direct_reference = f"container_service:{plugin_id}"
    if runtime is None:
        warnings.append(f"plugin {plugin_id!r} does not define a container_service runtime")
    else:
        direct_reference = _container_service_direct_reference(plugin_id, runtime)
        if runtime.image is not None:
            command = [
                sys.executable,
                "-m",
                "sfm_hub.container_runtime",
                "provision",
                plugin_id,
            ]
            warnings.append(
                "container_service install will provision a Docker service when "
                "the configured endpoint is local; otherwise attach to the configured service"
            )
        else:
            warnings.append(
                "container_service mode attaches to an already-running plugin service; "
                f"run `sfmapi plugins doctor {plugin_id}` to verify protocol health"
            )
    return InstallPlan(
        method="container_service",
        source=source,
        direct_reference=direct_reference,
        command=command,
        warnings=warnings,
    )


def build_external_tool_plan(plugin_id: str, *, source: GitHubSource) -> InstallPlan:
    return InstallPlan(
        method="external_tool",
        source=source,
        direct_reference=f"external_tool:{plugin_id}",
        command=[],
        warnings=[
            "external_tool mode records local executable use; run `sfmapi plugins doctor "
            f"{plugin_id}` to verify the tool"
        ],
    )


def run_uv_install(plan: InstallPlan) -> subprocess.CompletedProcess[str]:
    """Execute a planned uv install. Callers decide whether to persist state."""

    if plan.method != "uv":
        raise ValueError(f"run_uv_install requires a uv plan, got {plan.method!r}")
    return subprocess.run(plan.command, check=True, capture_output=True, text=True)


def run_install_command(plan: InstallPlan) -> subprocess.CompletedProcess[str] | None:
    """Execute an install plan that has a concrete local command."""

    if not plan.command:
        return None
    return subprocess.run(plan.command, check=True, capture_output=True, text=True)
