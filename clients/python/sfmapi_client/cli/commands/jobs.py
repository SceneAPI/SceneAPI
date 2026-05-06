"""`sfmapi jobs ...` — get / cancel / resume / events."""

from __future__ import annotations

import json as _json
import time

import click

from sfmapi_client.cli._render import render


@click.group()
def jobs() -> None:
    """Jobs and progress."""


@jobs.command("get")
@click.argument("job_id")
@click.pass_context
def get(ctx: click.Context, job_id: str) -> None:
    detail = ctx.obj["client"].get_job(job_id)
    if ctx.obj.get("json"):
        render(ctx, detail)
        return
    from rich.table import Table

    click.echo(f"Job {detail.job_id}  status={detail.status}  recipe={detail.recipe}")
    if detail.error_class:
        click.secho(f"  error: {detail.error_class}: {detail.error_message}", fg="red")
    t = Table(title="tasks")
    t.add_column("task_id", style="cyan")
    t.add_column("kind")
    t.add_column("status")
    t.add_column("cache_key", style="dim")
    for tk in detail.tasks:
        t.add_row(tk.task_id, tk.kind, tk.status, tk.cache_key[:12] + "…")
    render(ctx, detail, table=t)


@jobs.command("cancel")
@click.argument("job_id")
@click.option("--force", is_flag=True, help="Hard-kill (subprocess SIGKILL + worker restart).")
@click.pass_context
def cancel(ctx: click.Context, job_id: str, force: bool) -> None:
    j = ctx.obj["client"].cancel_job(job_id, force=force)
    render(ctx, j)


@jobs.command("resume")
@click.argument("job_id")
@click.pass_context
def resume(ctx: click.Context, job_id: str) -> None:
    j = ctx.obj["client"].resume_job(job_id)
    render(ctx, j)


@jobs.command("events")
@click.argument("job_id")
@click.option("--last-event-id", type=int, default=None)
@click.option("--follow/--no-follow", default=True, help="Stay open after the last event.")
@click.pass_context
def events(ctx: click.Context, job_id: str, last_event_id: int | None, follow: bool) -> None:
    """Tail SSE progress events for a job."""
    client = ctx.obj["client"]
    try:
        for ev in client.stream_events(job_id, last_event_id=last_event_id):
            if ctx.obj.get("json"):
                click.echo(_json.dumps(ev))
                continue
            kind = ev.get("kind", "?")
            phase = ev.get("phase") or ""
            extra = ""
            if kind == "phase_progress" and "current" in ev:
                extra = f" {ev['current']}"
                if ev.get("total"):
                    extra += f"/{ev['total']}"
            elif kind == "snapshot_available":
                extra = f" seq={ev.get('snapshot_seq')}"
            elif kind in ("warning", "error", "log_line"):
                extra = f" {ev.get('message', '')}"
            click.echo(f"[{ev.get('seq', '?'):>5}] {kind:<22} {phase:<22}{extra}")
            if not follow and kind == "phase_completed" and phase in ("export",):
                break
    except KeyboardInterrupt:
        click.secho("disconnected", fg="yellow", err=True)


@jobs.command("watch")
@click.argument("job_id")
@click.option("--interval", type=float, default=2.0, show_default=True)
@click.pass_context
def watch(ctx: click.Context, job_id: str, interval: float) -> None:
    """Poll `GET /jobs/{id}` until the job reaches a terminal state."""
    client = ctx.obj["client"]
    terminal = {"succeeded", "failed", "cancelled", "cancelled_dirty"}
    last_status = None
    while True:
        j = client.get_job(job_id)
        if j.status != last_status:
            click.echo(
                f"{j.status}: {sum(1 for t in j.tasks if t.status == 'succeeded')}/{len(j.tasks)} tasks succeeded"
            )
            last_status = j.status
        if j.status in terminal:
            return
        time.sleep(interval)
