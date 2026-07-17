"""Emit the FastAPI app's OpenAPI document to stdout or a file.

Used by:
  - the release workflow to ship `openapi.json` as an asset, and
  - the docs site to render an interactive API reference, and
  - downstream SDK code generators.

Usage:
    uv run python scripts/dump_openapi.py [--out PATH] [--format json|yaml]

The script is dialect-neutral: it does not need a database connection
because importing `sfmapi.server.main` does not touch the engine until lifespan
runs (and `app.openapi()` doesn't trigger lifespan).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write to PATH instead of stdout.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "yaml"),
        default="json",
        help="Output format. yaml requires PyYAML.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation. Use 0 for compact.",
    )
    args = parser.parse_args(argv)

    # Late import: this module is part of the same process as the app.
    from sfmapi.server.main import create_app

    app = create_app()
    spec = app.openapi()
    spec.setdefault("info", {})["x-generated-by"] = "scripts/dump_openapi.py"

    if args.format == "yaml":
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError:
            print("yaml format requires PyYAML (pip install pyyaml)", file=sys.stderr)
            return 2
        text = yaml.safe_dump(spec, sort_keys=False)
    else:
        text = json.dumps(spec, indent=args.indent or None, sort_keys=False)
        if args.indent:
            text += "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text):,} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
