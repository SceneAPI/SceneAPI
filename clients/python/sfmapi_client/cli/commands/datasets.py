"""`sfmapi datasets ...`."""

from __future__ import annotations

import json
from pathlib import Path

import click

from sfmapi_client.cli._render import render


@click.group()
def datasets() -> None:
    """Datasets and image sources."""


@datasets.command("create")
@click.argument("project_id")
@click.argument("name")
@click.option(
    "--source",
    "source_kind",
    type=click.Choice(("upload", "local", "s3"), case_sensitive=False),
    default="upload",
    show_default=True,
)
@click.option(
    "--entry",
    "entries",
    multiple=True,
    metavar="NAME:SHA",
    help="Upload-source entry. Repeat for multiple.",
)
@click.option("--root", default=None, help="Local-source root or S3 prefix.")
@click.option("--bucket", default=None, help="S3 bucket (when --source=s3).")
@click.option("--camera-model", default="SIMPLE_RADIAL", show_default=True)
@click.option(
    "--intrinsics-mode",
    type=click.Choice(("single_camera", "per_image", "per_folder")),
    default="single_camera",
    show_default=True,
)
@click.option("--spherical/--no-spherical", default=False)
@click.option("--exif-upright/--no-exif-upright", default=False)
@click.option(
    "--rig-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="JSON file with rig_config payload.",
)
@click.pass_context
def create(
    ctx: click.Context,
    project_id: str,
    name: str,
    source_kind: str,
    entries: tuple[str, ...],
    root: str | None,
    bucket: str | None,
    camera_model: str,
    intrinsics_mode: str,
    spherical: bool,
    exif_upright: bool,
    rig_config: Path | None,
) -> None:
    if source_kind == "upload":
        src: dict = {
            "kind": "upload",
            "entries": [_parse_entry(e) for e in entries],
        }
    elif source_kind == "local":
        if not root:
            raise click.UsageError("--root is required for --source=local")
        src = {"kind": "local", "root": root}
    else:
        if not bucket:
            raise click.UsageError("--bucket is required for --source=s3")
        src = {"kind": "s3", "bucket": bucket, "prefix": root or ""}
    rig = None
    if rig_config is not None:
        rig = json.loads(rig_config.read_text(encoding="utf-8"))
    ds = ctx.obj["client"].create_dataset(
        project_id,
        name=name,
        source=src,
        camera_model=camera_model,
        intrinsics_mode=intrinsics_mode,
        is_spherical=spherical,
        rig_config=rig,
        respect_exif_orientation=exif_upright,
    )
    render(ctx, ds)


def _parse_entry(s: str) -> dict[str, str]:
    if ":" not in s:
        raise click.UsageError(f"--entry must be NAME:SHA, got {s!r}")
    name, sha = s.split(":", 1)
    return {"name": name, "blob_sha": sha}


@datasets.command("get")
@click.argument("project_id")
@click.argument("dataset_id")
@click.pass_context
def get(ctx: click.Context, project_id: str, dataset_id: str) -> None:
    render(ctx, ctx.obj["client"].get_dataset(project_id, dataset_id))


@datasets.command("list")
@click.argument("project_id")
@click.pass_context
def list_(ctx: click.Context, project_id: str) -> None:
    rows = ctx.obj["client"].list_datasets(project_id)
    if ctx.obj.get("json"):
        render(ctx, rows)
        return
    from rich.table import Table

    t = Table(title=f"datasets in {project_id}")
    t.add_column("dataset_id", style="cyan")
    t.add_column("name")
    t.add_column("source_id", style="dim")
    t.add_column("camera")
    t.add_column("spherical")
    for r in rows:
        t.add_row(r.dataset_id, r.name, r.source_id, r.camera_model, str(r.is_spherical))
    render(ctx, rows, table=t)
