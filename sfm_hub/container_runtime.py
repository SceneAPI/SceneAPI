"""Docker provisioning helper for container_service plugin runtimes."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from sfm_hub.registry import get_manifest


def _safe_name(plugin_id: str) -> str:
    return "sfmapi-plugin-" + re.sub(r"[^a-z0-9_.-]+", "-", plugin_id.lower()).strip("-")


def _resolve_endpoint(runtime) -> tuple[str, int]:
    raw = None
    if runtime.service.url_env:
        raw = os.environ.get(runtime.service.url_env)
    raw = raw or runtime.service.default_url
    if not raw:
        env_hint = f"; set {runtime.service.url_env}" if runtime.service.url_env else ""
        raise RuntimeError(f"container service endpoint is not configured{env_hint}")
    parsed = urlsplit(raw)
    if parsed.scheme != "http" or not parsed.hostname:
        raise RuntimeError("automatic container_service provisioning requires an http:// endpoint")
    host = parsed.hostname.lower()
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        raise RuntimeError(
            "automatic container_service provisioning requires a local endpoint; "
            f"configured host is {parsed.hostname!r}"
        )
    return host, parsed.port or 80


def _image_ref(plugin_id: str, runtime) -> str:
    image = runtime.image
    if image is None:
        raise RuntimeError(f"plugin {plugin_id!r} does not define container image metadata")
    if image.image:
        return f"{image.image}@{image.digest}" if image.digest else image.image
    build = image.build
    if build is None:
        raise RuntimeError(f"plugin {plugin_id!r} container image has no image or build")

    tag = f"{_safe_name(plugin_id)}:{build.ref or 'latest'}"
    context = build.context
    if not context:
        context = get_manifest(plugin_id).github_url
    if build.source == "git" and build.ref and context.startswith("http"):
        context = f"{context}#{build.ref}"

    command = ["docker", "build", "-t", tag]
    for key, value in sorted(build.args.items()):
        command.extend(["--build-arg", f"{key}={value}"])
    if build.dockerfile:
        command.extend(["-f", build.dockerfile])
    command.append(context)
    subprocess.run(command, check=True)
    return tag


def _run_service(plugin_id: str, image_ref: str, runtime, host_port: int) -> None:
    name = _safe_name(plugin_id)
    container_port = int(os.environ.get("SFMAPI_PLUGIN_CONTAINER_PORT", "8080"))
    subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-p",
        f"127.0.0.1:{host_port}:{container_port}",
    ]
    if runtime.execution.gpu == "required":
        command.extend(["--gpus", "all"])
        command.extend(["-e", "NVIDIA_VISIBLE_DEVICES=all"])
        command.extend(["-e", "NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics"])
        if "TORCH_DEVICE" in runtime.execution.env:
            command.extend(["-e", "TORCH_DEVICE=cuda"])
    for env_name in sorted(set(runtime.execution.env + runtime.execution.secrets)):
        if env_name in os.environ:
            command.extend(["-e", env_name])
    for root_var in ("SFMAPI_WORKSPACE_ROOT", "SFMAPI_BLOB_ROOT", "SFMAPI_S3_CACHE_ROOT"):
        root = os.environ.get(root_var)
        if root:
            path = Path(root).resolve()
            command.extend(["-v", f"{path}:{path}"])
    command.append(image_ref)
    subprocess.run(command, check=True)


def provision(plugin_id: str) -> None:
    manifest = get_manifest(plugin_id)
    runtime = manifest.runtime_modes.container_service
    if runtime is None:
        raise RuntimeError(f"plugin {plugin_id!r} does not define a container_service runtime")
    _host, port = _resolve_endpoint(runtime)
    image = _image_ref(plugin_id, runtime)
    _run_service(plugin_id, image, runtime, port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("provision")
    p.add_argument("plugin_id")
    args = parser.parse_args(argv)
    if args.command == "provision":
        provision(args.plugin_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
