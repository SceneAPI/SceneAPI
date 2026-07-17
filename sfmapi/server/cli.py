"""Small operator CLI for sfmapi."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _serve(args: argparse.Namespace) -> None:
    if args.mcp is not None:
        os.environ["SFMAPI_MCP_MODE"] = args.mcp
    if args.profile is not None:
        from sfmapi.server.services import plugin_service

        plugin_service.use_default_profile(args.profile)
    import uvicorn

    uvicorn.run(
        "sfmapi.runtime:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def _mcp(args: argparse.Namespace) -> None:
    from sfmapi.server.mcp.server import main as mcp_main

    argv: list[str] = []
    if args.transport is not None:
        argv.extend(["--transport", args.transport])
    argv.extend(["--host", args.host, "--port", str(args.port), "--path", args.path])
    if args.allow_non_loopback:
        argv.append("--allow-non-loopback")
    mcp_main(argv)


def _check_backend(args: argparse.Namespace) -> None:
    if args.load_entry_points:
        from sfm_hub.discovery import load_backend_entry_points
        from sfmapi.server.adapters.registry import register_backend, register_backend_provider

        loaded = load_backend_entry_points(
            register_backend,
            register_provider=register_backend_provider,
        )
        errors = [item for item in loaded if item.load_error]
        for item in errors:
            print(f"Backend plugin load failed: {item.plugin_id}: {item.load_error}")
        for item in loaded:
            if item.skipped:
                print(f"Backend plugin skipped (disabled in hub state): {item.plugin_id}")
        if errors:
            raise SystemExit(1)
    for module in args.import_module:
        importlib.import_module(module)
    if args.backend is not None:
        os.environ["SFMAPI_BACKEND"] = args.backend

    from sfmapi.server.adapters.backend_contract import backend_contract_violations
    from sfmapi.server.adapters.registry import get_backend

    backend = get_backend(args.backend)
    violations = backend_contract_violations(backend)
    label = f"{getattr(backend, 'name', 'unknown')} {getattr(backend, 'version', '')}".strip()
    if not violations:
        print(f"OK backend contract: {label}")
        return
    print(f"Backend contract violations: {label}")
    for violation in violations:
        print(f"- {violation}")
    raise SystemExit(1)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _plugins_list(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    rows = plugin_service.list_plugins(args.query)
    if args.json:
        _print_json(rows)
        return
    for row in rows:
        status = "enabled" if row["enabled"] else "installed" if row["installed"] else "available"
        print(f"{row['plugin_id']}\t{status}\t{', '.join(row['providers'])}")


def _plugins_info(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    detail = plugin_service.get_plugin(args.plugin_id)
    _print_json(detail)


def _plugins_install(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    result = plugin_service.install_plugin(
        args.plugin_id,
        method=args.method,
        github_url=args.github,
        ref=args.ref,
        package_name=args.package,
        dry_run=args.dry_run,
        allow_unsafe_execution=not args.dry_run,
        request_id=args.request_id,
        provision_runtime=not args.no_provision_runtime,
        force=args.force,
    )
    _print_json(result)


def _plugins_enable(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    _print_json(plugin_service.enable_plugin(args.plugin_id))


def _plugins_disable(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    _print_json(plugin_service.disable_plugin(args.plugin_id))


def _plugins_doctor(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    _print_json(plugin_service.doctor_plugin(args.plugin_id))


def _plugins_detect_tools(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    _print_json(plugin_service.detect_tools())


def _plugins_entry_points(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    rows = plugin_service.list_entry_points(load=args.load)
    if args.json:
        _print_json(rows)
        return
    for row in rows:
        status = row["load_error"] or row.get("version") or ""
        print(f"{row['plugin_id']}\t{row['entry_point']}\t{status}")


def _providers_list(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    rows = plugin_service.list_providers()
    if args.json:
        _print_json(rows)
        return
    for row in rows:
        print(f"{row['provider_id']}\t{row['plugin_id']}\t{', '.join(row['capabilities'])}")


def _profiles_list(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    _print_json(plugin_service.routing_state())


def _profiles_create(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    routes: dict[str, list[str]] = {}
    if args.file:
        routes = json.loads(Path(args.file).read_text(encoding="utf-8"))
    for item in args.route:
        stage, _, providers = item.partition("=")
        if not stage or not providers:
            raise SystemExit("--route must use stage=provider1,provider2")
        routes[stage] = [provider.strip() for provider in providers.split(",") if provider.strip()]
    _print_json(plugin_service.create_profile(args.name, routes))


def _profiles_set_default(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    _print_json(plugin_service.use_default_profile(args.name))


def _profiles_assign_project(args: argparse.Namespace) -> None:
    from sfmapi.server.services import plugin_service

    _print_json(plugin_service.assign_project_profile(args.project_id, args.name))


def _profiles_assign_workspace(args: argparse.Namespace) -> None:
    from sfmapi.server.core.config import get_settings
    from sfmapi.server.services import plugin_service

    workspace = args.workspace or str(get_settings().workspace_root)
    _print_json(plugin_service.assign_workspace_profile(workspace, args.name))


def _scaffold_plugin(args: argparse.Namespace) -> None:
    from sfmapi.server.scaffolding import scaffold_plugin

    output_dir = Path(args.output_dir).expanduser().resolve()
    written = scaffold_plugin(
        args.plugin_id,
        output_dir=output_dir,
        display_name=args.display_name,
        description=args.description,
        vendor=args.vendor,
        overwrite=args.overwrite,
    )
    root = output_dir / f"sfmapi_{args.plugin_id}"
    print(f"scaffolded {len(written)} files in {root}")
    for entry in written:
        print(f"  {entry.path.relative_to(output_dir)} ({entry.bytes_written} bytes)")
    print()
    print("Next: cd into the directory and run `uv pip install -e .` to register the entry point.")


def _scaffold_contract(args: argparse.Namespace) -> None:
    from sfmapi.server import scaffolding

    # sfmapi/server/core lives next to this CLI module; tests/unit is repo-relative.
    server_dir = Path(scaffolding.__file__).resolve().parent
    repo_root = server_dir.parents[1]  # <repo>/sfmapi/server -> <repo>
    core_dir = Path(args.core_dir).expanduser().resolve() if args.core_dir else server_dir / "core"
    tests_dir = (
        Path(args.tests_dir).expanduser().resolve()
        if args.tests_dir
        else repo_root / "tests" / "unit"
    )
    written = scaffolding.scaffold_contract(
        args.name,
        core_dir=core_dir,
        tests_dir=tests_dir,
        title=args.title,
        overwrite=args.overwrite,
    )
    print(f"scaffolded {len(written)} files for contract {args.name!r}:")
    for entry in written:
        print(f"  {entry.path} ({entry.bytes_written} bytes)")
    print()
    print("Next steps:")
    print(
        f"  1. flesh out contract_dict() in sfmapi/server/core/{args.name}.py with the real standard"
    )
    print("  2. register it in sfmapi-cpp/tools/gen_contracts.py:")
    print(f"       from sfmapi.server.core import {args.name}")
    print(f"       CONTRACTS = {{..., {args.name}.CONTRACT_NAME: {args.name}.contract_dict}}")
    print("  3. run:  uv run python ../sfmapi-cpp/tools/gen_contracts.py")
    print(
        "  4. commit the sfmapi/server/core module, the test, and the generated "
        "parity/contracts + src/contracts artifacts."
    )
    print("  The contract-coverage gate enforces all four are present.")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sfmapi")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Run the sfmapi REST API.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument(
        "--profile",
        default=None,
        help="Set the default sfm_hub routing profile before serving.",
    )
    serve.add_argument(
        "--mcp",
        choices=("off", "local"),
        default=None,
        help="Set the API-process MCP mode for this run.",
    )
    serve.set_defaults(func=_serve)

    mcp = subcommands.add_parser("mcp", help="Run the sfmapi MCP adapter.")
    mcp.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=None,
        help="MCP transport. Defaults to SFMAPI_MCP_MODE when it is stdio/http, otherwise stdio.",
    )
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=9000)
    mcp.add_argument("--path", default="/mcp")
    mcp.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow HTTP binding to a non-loopback host.",
    )
    mcp.set_defaults(func=_mcp)

    check_backend = subcommands.add_parser(
        "check-backend",
        help="Validate backend capabilities, actions, and backend_options schemas.",
    )
    check_backend.add_argument(
        "--import",
        dest="import_module",
        action="append",
        default=[],
        help="Import a backend package/module before resolving SFMAPI_BACKEND.",
    )
    check_backend.add_argument(
        "--backend",
        default=None,
        help="Backend registry name to check. Defaults to SFMAPI_BACKEND.",
    )
    check_backend.add_argument(
        "--load-entry-points",
        action="store_true",
        help='Load installed [project.entry-points."sfmapi.backends"] before checking.',
    )
    check_backend.set_defaults(func=_check_backend)

    plugins = subcommands.add_parser("plugins", help="Discover and manage sfmapi backend plugins.")
    plugin_subcommands = plugins.add_subparsers(dest="plugin_command", required=True)

    plugins_list = plugin_subcommands.add_parser("list", help="List registered plugins.")
    plugins_list.add_argument("--query", default=None)
    plugins_list.add_argument("--json", action="store_true")
    plugins_list.set_defaults(func=_plugins_list)

    plugins_search = plugin_subcommands.add_parser("search", help="Search registered plugins.")
    plugins_search.add_argument("query")
    plugins_search.add_argument("--json", action="store_true")
    plugins_search.set_defaults(func=_plugins_list)

    plugins_info = plugin_subcommands.add_parser("info", help="Read one plugin manifest.")
    plugins_info.add_argument("plugin_id")
    plugins_info.set_defaults(func=_plugins_info)

    plugins_install = plugin_subcommands.add_parser("install", help="Install a plugin.")
    plugins_install.add_argument("plugin_id")
    plugins_install.add_argument(
        "--method", choices=("uv", "docker", "container_service", "external_tool"), default="uv"
    )
    plugins_install.add_argument("--github", default=None, help="Install from a GitHub URL.")
    plugins_install.add_argument("--ref", default=None, help="Git branch, tag, or commit.")
    plugins_install.add_argument("--package", default=None, help="Python package name.")
    plugins_install.add_argument("--request-id", default=None, help="UUID-style idempotency key.")
    plugins_install.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the install without running commands or recording state.",
    )
    plugins_install.add_argument(
        "--no-provision-runtime",
        action="store_true",
        help="Install only the wrapper package; skip plugin-owned runtime provisioning.",
    )
    plugins_install.add_argument(
        "--force",
        action="store_true",
        help="Install even if the manifest's host compatibility (os/python) does not match.",
    )
    plugins_install.set_defaults(func=_plugins_install)

    plugins_enable = plugin_subcommands.add_parser("enable", help="Enable a plugin.")
    plugins_enable.add_argument("plugin_id")
    plugins_enable.set_defaults(func=_plugins_enable)

    plugins_disable = plugin_subcommands.add_parser("disable", help="Disable a plugin.")
    plugins_disable.add_argument("plugin_id")
    plugins_disable.set_defaults(func=_plugins_disable)

    plugins_doctor = plugin_subcommands.add_parser("doctor", help="Run plugin diagnostics.")
    plugins_doctor.add_argument("plugin_id")
    plugins_doctor.set_defaults(func=_plugins_doctor)

    plugins_detect = plugin_subcommands.add_parser(
        "detect-tools", help="Detect installed SfM tools."
    )
    plugins_detect.set_defaults(func=_plugins_detect_tools)

    plugins_entry_points = plugin_subcommands.add_parser(
        "entry-points", help="List installed Python backend entry points."
    )
    plugins_entry_points.add_argument("--load", action="store_true")
    plugins_entry_points.add_argument("--json", action="store_true")
    plugins_entry_points.set_defaults(func=_plugins_entry_points)

    providers = subcommands.add_parser("providers", help="Inspect enabled backend providers.")
    provider_subcommands = providers.add_subparsers(dest="provider_command", required=True)
    providers_list = provider_subcommands.add_parser("list", help="List enabled providers.")
    providers_list.add_argument("--json", action="store_true")
    providers_list.set_defaults(func=_providers_list)

    profiles = subcommands.add_parser("profiles", help="Manage provider routing profiles.")
    profile_subcommands = profiles.add_subparsers(dest="profile_command", required=True)
    profiles_list = profile_subcommands.add_parser("list", help="List routing profiles.")
    profiles_list.set_defaults(func=_profiles_list)
    profiles_create = profile_subcommands.add_parser("create", help="Create or replace a profile.")
    profiles_create.add_argument("name")
    profiles_create.add_argument(
        "--route",
        action="append",
        default=[],
        help="Route in stage=provider1,provider2 form. Repeatable.",
    )
    profiles_create.add_argument(
        "--file", default=None, help="JSON file mapping stage to providers."
    )
    profiles_create.set_defaults(func=_profiles_create)
    profiles_default = profile_subcommands.add_parser("set-default", help="Set default profile.")
    profiles_default.add_argument("name")
    profiles_default.set_defaults(func=_profiles_set_default)
    profiles_project = profile_subcommands.add_parser(
        "assign-project", help="Assign a routing profile to a project id."
    )
    profiles_project.add_argument("project_id")
    profiles_project.add_argument("name")
    profiles_project.set_defaults(func=_profiles_assign_project)
    profiles_workspace = profile_subcommands.add_parser(
        "assign-workspace", help="Assign a routing profile to a workspace root."
    )
    profiles_workspace.add_argument("name")
    profiles_workspace.add_argument("--workspace", default=None)
    profiles_workspace.set_defaults(func=_profiles_assign_workspace)

    scaffold = subcommands.add_parser(
        "scaffold-plugin",
        help=(
            "Scaffold a minimal sfmapi backend plugin: pyproject.toml + "
            "src/sfmapi_<id>/{plugin,backend,__init__}.py + tests + README. "
            "Uses the canonical sfmapi.backends.Plugin class."
        ),
    )
    scaffold.add_argument(
        "plugin_id",
        help=(
            "Lowercase plugin id (used as the entry-point name, package "
            "suffix, and backend name). Must match [a-z][a-z0-9_]*."
        ),
    )
    scaffold.add_argument(
        "--output-dir",
        default=".",
        help="Directory to create sfmapi_<plugin_id>/ inside. Defaults to cwd.",
    )
    scaffold.add_argument(
        "--display-name",
        default=None,
        help="Human-readable display name. Defaults to TitleCase(plugin_id).",
    )
    scaffold.add_argument(
        "--description",
        default=None,
        help="One-line plugin description used in the manifest + README.",
    )
    scaffold.add_argument(
        "--vendor",
        default="unknown",
        help="Vendor name surfaced in the backend's runtime_versions metadata.",
    )
    scaffold.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace any existing scaffolded files instead of erroring.",
    )
    scaffold.set_defaults(func=_scaffold_plugin)

    scaffold_contract = subcommands.add_parser(
        "scaffold-contract",
        help=(
            "Scaffold an off-wire core contract: sfmapi/server/core/<name>.py "
            "(CONTRACT_NAME + contract_dict) + tests/unit/test_<name>_contract.py. "
            "Prints the one cross-repo registration step."
        ),
    )
    scaffold_contract.add_argument(
        "name",
        help=(
            "Lowercase contract name (module name, test/artifact filename, "
            "C++ accessor stem). Must match [a-z][a-z0-9_]*."
        ),
    )
    scaffold_contract.add_argument(
        "--title",
        default=None,
        help="Human-readable title in the module docstring. Defaults to TitleCase(name).",
    )
    scaffold_contract.add_argument(
        "--core-dir",
        default=None,
        help="Override the sfmapi/server/core directory (defaults to the installed sfmapi/server/core).",
    )
    scaffold_contract.add_argument(
        "--tests-dir",
        default=None,
        help="Override the tests/unit directory (defaults to the repo's tests/unit).",
    )
    scaffold_contract.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing files instead of erroring.",
    )
    scaffold_contract.set_defaults(func=_scaffold_contract)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
