"""`python -m bench.cli {run, lint, list, plot}`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from bench import harness, store
from sfmapi_client import SfmApiClient

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


def _list_dataset_files() -> list[Path]:
    return sorted(DATASETS_DIR.glob("*.yaml"))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """sfmapi benchmark harness."""


@cli.command("list")
def list_() -> None:
    """List the dataset specs available."""
    for p in _list_dataset_files():
        spec = harness.load_dataset(p)
        click.echo(
            f"{spec.name:<20} recipe={spec.recipe:<14} {spec.description.splitlines()[0] if spec.description else ''}"
        )


@cli.command("run")
@click.option(
    "--dataset", "datasets", multiple=True, help="Dataset name (no .yaml). Repeat for several."
)
@click.option("--all", "run_all", is_flag=True, help="Run every dataset under bench/datasets/.")
@click.option(
    "--base-url", envvar="SFMAPI_BASE_URL", default="http://localhost:8080", show_default=True
)
@click.option("--api-key", envvar="SFMAPI_KEY", default=None)
@click.option(
    "--timeout", type=float, default=3600.0, show_default=True, help="Per-bench timeout in seconds."
)
@click.option("--poll-interval", type=float, default=2.0, show_default=True)
def run(
    datasets: tuple[str, ...],
    run_all: bool,
    base_url: str,
    api_key: str | None,
    timeout: float,
    poll_interval: float,
) -> None:
    """Drive one or more benchmark datasets through the live server."""
    targets: list[Path]
    if run_all:
        targets = _list_dataset_files()
    elif datasets:
        targets = []
        for name in datasets:
            p = DATASETS_DIR / f"{name}.yaml"
            if not p.is_file():
                raise click.UsageError(f"unknown dataset: {name}")
            targets.append(p)
    else:
        raise click.UsageError("pass --dataset NAME (repeatable) or --all")

    failed = 0
    with SfmApiClient(base_url, api_key=api_key, timeout=120.0) as client:
        for spec_path in targets:
            spec = harness.load_dataset(spec_path)
            click.secho(f"[bench] {spec.name} ({spec.recipe}) ...", fg="cyan")
            try:
                result = harness.run_one(
                    client=client,
                    dataset=spec,
                    poll_interval=poll_interval,
                    timeout_seconds=timeout,
                )
            except Exception as e:  # noqa: BLE001 — record + continue
                click.secho(f"  FAILED: {type(e).__name__}: {e}", fg="red")
                failed += 1
                continue
            store.append(result)
            click.secho(
                f"  status={result.status} wall={result.wall_seconds:.1f}s "
                f"reg={int(result.metrics.get('num_reg_images', 0))} "
                f"pts={int(result.metrics.get('num_points3D', 0))}",
                fg=("green" if result.status == "succeeded" else "yellow"),
            )
    sys.exit(1 if failed else 0)


@cli.command("lint")
@click.option(
    "--tolerance", type=float, default=None, help="Override per-metric tolerance (e.g. 0.05 = 5%)."
)
@click.option(
    "--latest-from", default=None, help="Specific git_sha to lint (defaults to most-recent .jsonl)."
)
@click.option("--exit-zero", is_flag=True, help="Always exit 0 (just print findings).")
def lint(tolerance: float | None, latest_from: str | None, exit_zero: bool) -> None:
    """Compare the latest results against the rolling median of history."""
    if latest_from:
        target = store.RESULTS_DIR / f"{latest_from}.jsonl"
        if not target.is_file():
            raise click.UsageError(f"no results for git_sha={latest_from}")
        files = [target]
    else:
        files = sorted(store.RESULTS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            click.secho("no results yet — nothing to lint", fg="yellow")
            sys.exit(0)
        files = files[-1:]
    latest_sha = files[0].stem

    latest_results: list[store.BenchResult] = []
    with files[0].open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            latest_results.append(store.BenchResult(**json.loads(line)))
    if not latest_results:
        click.secho(f"no rows in {files[0]}", fg="yellow")
        sys.exit(0)

    regressions = store.lint(latest_results, tolerance_override=tolerance)
    click.echo(
        f"linting {len(latest_results)} result(s) for sha={latest_sha} against rolling median..."
    )
    for reg in regressions:
        click.secho("  REGRESSION " + reg.as_text(), fg="red")
    if not regressions:
        click.secho("  OK — no regressions vs history", fg="green")
    sys.exit(0 if (exit_zero or not regressions) else 1)


@cli.command("history")
@click.option("--dataset", default=None, help="Filter to one dataset.")
@click.option("--recipe", default=None, help="Filter to one recipe kind.")
@click.option("--limit", type=int, default=20, show_default=True)
def history(dataset: str | None, recipe: str | None, limit: int) -> None:
    """Print the last N rows for a (dataset, recipe) combo."""
    rows = list(store.iter_history())
    if dataset:
        rows = [r for r in rows if r.dataset == dataset]
    if recipe:
        rows = [r for r in rows if r.recipe == recipe]
    rows = rows[-limit:]
    if not rows:
        click.secho("no matching rows", fg="yellow")
        return
    for r in rows:
        m = r.metrics
        click.echo(
            f"{r.finished_at}  sha={r.git_sha[:8]:<10} {r.dataset:<14} {r.recipe:<14} "
            f"status={r.status:<10} wall={m.get('wall_seconds', 0):.1f}s "
            f"reg={int(m.get('num_reg_images', 0)):<5} pts={int(m.get('num_points3D', 0))}"
        )


def main() -> int:
    try:
        cli.main(prog_name="bench", standalone_mode=False)
    except click.exceptions.Exit as e:
        return int(e.exit_code or 0)
    except click.ClickException as e:
        e.show()
        return e.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
