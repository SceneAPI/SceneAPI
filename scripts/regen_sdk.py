"""Regenerate SDK artifacts in the sibling sfmapi-sdk repository.

Workflow:
  1. Dump a fresh OpenAPI document from the FastAPI app.
  2. Run ``openapi-python-client generate`` into
     ``../sfmapi-sdk/python/sfmapi_client_gen/``.
  3. Regenerate TypeScript OpenAPI types under
     ``../sfmapi-sdk/typescript/src/_generated/``.
  4. Print a summary of generated models + endpoint methods.

Set ``SFMAPI_SDK_REPO`` to point at a different SDK checkout. The
server repo remains the OpenAPI source of truth; the SDK repo owns
packaging and generated client artifacts.

Usage:
    uv run python scripts/regen_sdk.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_REPO = Path(os.environ.get("SFMAPI_SDK_REPO", REPO_ROOT.parent / "sfmapi-sdk")).resolve()
SPEC_PATH = REPO_ROOT / "openapi.json"
SDK_SPEC_PATH = SDK_REPO / "openapi.json"
OUT_DIR = SDK_REPO / "python" / "sfmapi_client_gen"
TS_ROOT = SDK_REPO / "typescript"
TS_OUT_DIR = TS_ROOT / "src" / "_generated"
DUMP_SCRIPT = REPO_ROOT / "scripts" / "dump_openapi.py"

# Files in the generated SDK that the repo owns and the generator
# must NOT overwrite. We snapshot them before regen and restore
# afterwards (`openapi-python-client --overwrite` wipes the whole dir).
PYTHON_METADATA_FILES = ("pyproject.toml", "README.md", "py.typed", "_ergonomics.py")
_metadata_cache: dict[str, str] = {}


def _snapshot_python_metadata() -> None:
    _metadata_cache.clear()
    for name in PYTHON_METADATA_FILES:
        p = OUT_DIR / name
        if p.is_file():
            _metadata_cache[name] = p.read_text(encoding="utf-8")


def _restore_python_metadata() -> None:
    for name, content in _metadata_cache.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8")
    if _metadata_cache:
        print(f"-> restored {len(_metadata_cache)} package metadata file(s)")


def main() -> int:
    if not DUMP_SCRIPT.is_file():
        print(f"missing {DUMP_SCRIPT}", file=sys.stderr)
        return 2
    if not SDK_REPO.is_dir():
        print(f"missing SDK repo: {SDK_REPO}", file=sys.stderr)
        return 2

    # 1. Dump OpenAPI.
    print(f"-> dumping OpenAPI to {SPEC_PATH}")
    rc = subprocess.run(
        [sys.executable, str(DUMP_SCRIPT), "--out", str(SPEC_PATH), "--indent", "0"],
        check=False,
    ).returncode
    if rc != 0:
        return rc
    shutil.copyfile(SPEC_PATH, SDK_SPEC_PATH)
    print(f"-> copied OpenAPI snapshot to {SDK_SPEC_PATH}")

    # 2. Generate. Snapshot non-generated files before --overwrite
    # nukes the directory.
    if not shutil.which("uvx"):
        print("uvx not on PATH (need `uv` installed)", file=sys.stderr)
        return 2
    _snapshot_python_metadata()
    print(f"-> regenerating SDK at {OUT_DIR}")
    rc = subprocess.run(
        [
            "uvx",
            "openapi-python-client",
            "generate",
            "--path",
            str(SPEC_PATH),
            "--output-path",
            str(OUT_DIR),
            "--overwrite",
            "--meta",
            "none",
        ],
        check=False,
    ).returncode
    if rc != 0:
        return rc

    # 3. Restore non-generated package metadata that openapi-python-client
    # wipes on every regen (--meta none doesn't generate them, but
    # --overwrite removes everything else in the directory). The repo
    # owns these files; the generator owns api/, models/, and the
    # client/errors/types.py trio.
    _restore_python_metadata()

    # 4. Summary (Python).
    n_models = len(list((OUT_DIR / "models").glob("*.py"))) - 1  # exclude __init__
    n_apis = sum(
        1 for p in (OUT_DIR / "api").rglob("*.py") if p.name not in {"__init__.py", "__pycache__"}
    )
    print(f"OK Python SDK: {n_models} model files, {n_apis} endpoint methods")

    # 4. TypeScript types.
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        print(
            "skipping TS generation (npx not on PATH); run "
            f"`npm run gen:sdk` from {TS_ROOT} to generate"
        )
        return 0
    TS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts_target = TS_OUT_DIR / "openapi.d.ts"
    print(f"-> regenerating TS types at {ts_target}")
    rc = subprocess.run(
        [
            npx,
            "openapi-typescript",
            str(SPEC_PATH),
            "-o",
            str(ts_target),
        ],
        cwd=TS_ROOT,
        check=False,
        shell=False,
    ).returncode
    if rc != 0:
        return rc
    n_lines = ts_target.read_text(encoding="utf-8").count("\n")
    print(f"OK TypeScript SDK: {ts_target.name} ({n_lines} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
