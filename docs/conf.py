"""Sphinx configuration for sfmapi documentation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the package + sdk importable for autodoc.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "clients" / "python"))

# Allow autodoc to work without server-side extras installed (e.g.,
# on the GitHub Pages build runner without arq's redis client or
# the postgres drivers).
autodoc_mock_imports = [
    "arq",
    "redis",
    "asyncpg",
    "psycopg",
    "boto3",
    "PIL",
    "numpy",
]

# -- Project information -----------------------------------------------------

project = "sfmapi"
author = "the sfmapi authors"
copyright = "2026, the sfmapi authors"  # noqa: A001 — sphinx convention

try:
    from app import __version__ as version
except ImportError:
    version = "0.0.1"
release = version

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.mermaid",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "linkify",
    "tasklist",
    "attrs_inline",
]
myst_heading_anchors = 3
linkify_fuzzy_links = False

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- HTML output -------------------------------------------------------------

html_theme = "furo"
html_title = f"sfmapi {release}"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["custom.js"]
html_logo = "_static/logo.svg"
html_favicon = "_static/favicon.svg"
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    "source_repository": "https://github.com/SFMAPI/sfmapi",
    "source_branch": "main",
    "source_directory": "docs/",
    # Color tokens are driven by custom.css; these line up with the
    # `--sfm-paper`, `--sfm-ink`, and `--sfm-accent` palette.
    "light_css_variables": {
        "color-brand-primary": "#b45309",
        "color-brand-content": "#b45309",
        "color-background-primary": "#f7f4ed",
        "color-foreground-primary": "#1b1812",
    },
    "dark_css_variables": {
        "color-brand-primary": "#e2924c",
        "color-brand-content": "#e2924c",
        "color-background-primary": "#15140f",
        "color-foreground-primary": "#ede7da",
    },
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/SFMAPI/sfmapi",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" '
                'viewBox="0 0 16 16" height="1.2em" width="1.2em" '
                'xmlns="http://www.w3.org/2000/svg">'
                '<path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 '
                "2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 "
                "0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 "
                "1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 "
                "0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 "
                "0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 "
                "2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 "
                "1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 "
                '1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>'
                "</svg>"
            ),
            "class": "",
        }
    ],
    "announcement": (
        "<strong>Pre-release.</strong> The wire surface is stable and "
        "tests are green, but version &lt;1.0 may break shapes between "
        "minor releases. "
        '<a href="guides/quickstart.html" style="color:white">'
        "Get started in 5 minutes →</a>"
    ),
}

# -- Autodoc + autosummary ---------------------------------------------------

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
autodoc_typehints_format = "short"
autosummary_generate = True

# -- Intersphinx -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
# Best-effort tolerance: don't fail the strict build if an upstream
# inventory 404s temporarily.
intersphinx_disabled_reftypes = ["*"]
intersphinx_timeout = 10

# -- Misc --------------------------------------------------------------------

# Bundle assets that need to live at the SITE root (e.g., CNAME, robots.txt,
# .nojekyll). Anything in `docs/_extra/` is copied into the build output.
html_extra_path: list[str] = []
_extra = ROOT / "docs" / "_extra"
if _extra.is_dir():
    html_extra_path.append(str(_extra))


def _generate_openapi() -> None:
    """Emit the FastAPI OpenAPI doc into _static/openapi.json so the
    Swagger UI page (reference/openapi.md) can fetch it without a
    network round-trip and any release artifact pipeline can pick it
    up from a known path."""
    target = Path(__file__).parent / "_static" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from app.main import create_app

        spec = create_app().openapi()
    except Exception as exc:  # noqa: BLE001 — best-effort docs build
        target.write_text(f'{{"error": "openapi unavailable: {exc}"}}', encoding="utf-8")
        return
    import json as _json

    target.write_text(_json.dumps(spec, indent=2), encoding="utf-8")


_generate_openapi()

nitpicky = False  # toggle to true once cross-references stabilize
suppress_warnings = [
    "myst.header",
    # SFMAPI-SPEC.md uses `json` blocks for human-readable schemas
    # (`"a": "x" | null`, `// JS-style comments`). Pygments warns;
    # harmless.
    "misc.highlighting_failure",
]

# Used by the index page banner
rst_epilog = f"""
.. |version| replace:: {release}
"""

# Read the Docs sets these env vars; surface them for the navbar.
on_rtd = os.environ.get("READTHEDOCS") == "True"
html_context = {
    "display_github": True,
    "github_user": "SFMAPI",
    "github_repo": "sfmapi",
    "github_version": "main",
    "conf_py_path": "/docs/",
}
