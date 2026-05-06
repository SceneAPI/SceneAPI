"""sfmapi CLI entrypoint.

Composed of small command groups under `sfmapi_client.cli.commands.*`.
Each command is a thin wrapper around the SDK; the CLI does no
business logic of its own.

Common flags (set on every group):
    --base-url URL          [env: SFMAPI_BASE_URL] default http://localhost:8080
    --api-key  KEY          [env: SFMAPI_KEY]      default unset
    --json                  emit raw JSON instead of Rich tables
    --timeout SECONDS       per-request timeout
"""

from __future__ import annotations

import sys
from typing import Any

import click

from sfmapi_client import SfmApiClient, SfmApiError, __version__
from sfmapi_client.cli import commands


def _print_error(err: SfmApiError) -> None:
    click.secho(f"sfmapi error ({err.status_code}): {err}", fg="red", err=True)
    if err.problem:
        click.secho(f"  problem: {err.problem}", fg="red", err=True)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version")
@click.option(
    "--base-url",
    envvar="SFMAPI_BASE_URL",
    default="http://localhost:8080",
    show_default=True,
    help="Base URL of the sfmapi server.",
)
@click.option(
    "--api-key",
    envvar="SFMAPI_KEY",
    default=None,
    help="API key for `auth_mode=api_key` deployments.",
)
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--json",
    "emit_json",
    is_flag=True,
    help="Emit raw JSON instead of Rich-rendered tables.",
)
@click.pass_context
def cli(
    ctx: click.Context, base_url: str, api_key: str | None, timeout: float, emit_json: bool
) -> None:
    """sfmapi command-line interface."""
    ctx.ensure_object(dict)
    ctx.obj["client"] = SfmApiClient(base_url, api_key=api_key, timeout=timeout)
    ctx.obj["json"] = emit_json
    ctx.call_on_close(lambda: ctx.obj["client"].close())


# Register command groups.
cli.add_command(commands.health.health)
cli.add_command(commands.projects.projects)
cli.add_command(commands.uploads.uploads)
cli.add_command(commands.datasets.datasets)
cli.add_command(commands.images.images)
cli.add_command(commands.jobs.jobs)
cli.add_command(commands.pipelines.pipelines)
cli.add_command(commands.snapshots.snapshots)


def main(argv: list[str] | None = None) -> int:
    """Programmatic entrypoint. Returns the click exit code."""
    try:
        cli.main(args=argv, prog_name="sfmapi", standalone_mode=False)
    except click.exceptions.Exit as e:
        return int(e.exit_code or 0)
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except SfmApiError as e:
        _print_error(e)
        return 2
    except KeyboardInterrupt:
        click.secho("interrupted", fg="yellow", err=True)
        return 130
    except Exception as e:
        click.secho(f"unexpected error: {e}", fg="red", err=True)
        return 1
    return 0


def _entrypoint() -> None:  # pragma: no cover — sys.exit wrapper
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    _entrypoint()


# `render` lives in `sfmapi_client.cli._render` to avoid a circular
# import between this module and the command groups it registers.
from sfmapi_client.cli._render import render  # noqa: E402, F401  re-exported

del Any  # type: ignore[name-defined]  (was used in earlier draft)
