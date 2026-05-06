"""`sfmapi images ...`."""

from __future__ import annotations

import click

from sfmapi_client.cli._render import render


@click.group()
def images() -> None:
    """Images inside a dataset."""


@images.command("add")
@click.argument("dataset_id")
@click.argument("name")
@click.option("--blob-sha", default=None, help="From `sfmapi uploads file`.")
@click.option("--rel-path", default=None, help="For local-source datasets.")
@click.pass_context
def add(
    ctx: click.Context,
    dataset_id: str,
    name: str,
    blob_sha: str | None,
    rel_path: str | None,
) -> None:
    if not blob_sha and not rel_path:
        raise click.UsageError("--blob-sha or --rel-path required")
    img = ctx.obj["client"].add_image(dataset_id, name=name, blob_sha=blob_sha, rel_path=rel_path)
    render(ctx, img)


@images.command("list")
@click.argument("dataset_id")
@click.option("--page-size", type=int, default=100)
@click.option("--page-token", default=None)
@click.pass_context
def list_(ctx: click.Context, dataset_id: str, page_size: int, page_token: str | None) -> None:
    page = ctx.obj["client"].list_images(dataset_id, page_size=page_size, page_token=page_token)
    if ctx.obj.get("json"):
        render(ctx, page)
        return
    from rich.table import Table

    t = Table(title=f"images in {dataset_id}")
    t.add_column("name")
    t.add_column("content_sha", style="dim")
    t.add_column("source")
    t.add_column("size")
    for r in page.items:
        t.add_row(r.name, r.content_sha[:12] + "…", r.source_kind, str(r.byte_size or "-"))
    render(ctx, page.items, table=t)
