"""`sfmapi uploads ...` — file upload helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from sfmapi_client.cli._render import render


@click.group()
def uploads() -> None:
    """Chunked uploads."""


@uploads.command("file")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--content-type", default=None, help="Content-Type override.")
@click.option(
    "--idempotency-key",
    default=None,
    help="Idempotency-Key header value (replays return same upload_id).",
)
@click.pass_context
def upload_file(
    ctx: click.Context,
    path: Path,
    content_type: str | None,
    idempotency_key: str | None,
) -> None:
    """Upload a single file end-to-end. Prints the resulting blob sha."""
    data = path.read_bytes()
    ctype = content_type or _guess_mime(path)
    sha = ctx.obj["client"].upload_bytes(
        data,
        content_type=ctype,
        idempotency_key=idempotency_key,
    )
    if ctx.obj.get("json"):
        render(
            ctx,
            {
                "blob_sha": sha,
                "byte_size": len(data),
                "content_type": ctype,
                "path": str(path),
            },
        )
    else:
        click.echo(sha)


@uploads.command("dir")
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--ext",
    multiple=True,
    default=("jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp"),
    show_default=True,
    help="File extensions to upload (repeat for multiple).",
)
@click.pass_context
def upload_dir(ctx: click.Context, directory: Path, ext: tuple[str, ...]) -> None:
    """Upload every image under DIRECTORY. Prints `name<TAB>sha` per file."""
    suffixes = {f".{e.lower().lstrip('.')}" for e in ext}
    files = sorted(p for p in directory.rglob("*") if p.suffix.lower() in suffixes and p.is_file())
    if not files:
        click.secho(
            f"no files matching {sorted(suffixes)} under {directory}", fg="yellow", err=True
        )
        sys.exit(1)
    client = ctx.obj["client"]
    for p in files:
        rel = p.relative_to(directory).as_posix()
        sha = client.upload_bytes(p.read_bytes(), content_type=_guess_mime(p))
        click.echo(f"{rel}\t{sha}")


def _guess_mime(path: Path) -> str | None:
    import mimetypes

    return mimetypes.guess_type(str(path))[0]
