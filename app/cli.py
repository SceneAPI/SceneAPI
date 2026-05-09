"""Small operator CLI for sfmapi."""

from __future__ import annotations

import argparse
import importlib
import os
from collections.abc import Sequence


def _serve(args: argparse.Namespace) -> None:
    if args.mcp is not None:
        os.environ["SFMAPI_MCP_MODE"] = args.mcp
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def _mcp(args: argparse.Namespace) -> None:
    from app.mcp.server import main as mcp_main

    argv: list[str] = []
    if args.transport is not None:
        argv.extend(["--transport", args.transport])
    argv.extend(["--host", args.host, "--port", str(args.port), "--path", args.path])
    if args.allow_non_loopback:
        argv.append("--allow-non-loopback")
    mcp_main(argv)


def _check_backend(args: argparse.Namespace) -> None:
    for module in args.import_module:
        importlib.import_module(module)
    if args.backend is not None:
        os.environ["SFMAPI_BACKEND"] = args.backend

    from app.adapters.backend_contract import backend_contract_violations
    from app.adapters.registry import get_backend

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


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sfmapi")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Run the sfmapi REST API.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
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
    check_backend.set_defaults(func=_check_backend)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
