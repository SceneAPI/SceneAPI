"""Standalone runner: `python -m tests.conformance --base-url ...`.

Wraps pytest so external implementers can run the conformance suite
against their server without learning pytest's CLI.

Exit codes match pytest:
  0 = all required checks passed (skips OK)
  1 = at least one MUST failed
  2..5 = pytest internal errors
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sfmapi-conformance",
        description="Run the sfmapi conformance suite against a target server.",
    )
    parser.add_argument(
        "--base-url",
        required=False,
        help=(
            "Base URL of the server under test. If omitted, runs against "
            "the in-process reference implementation."
        ),
    )
    parser.add_argument(
        "--api-key",
        required=False,
        help="Bearer token (when the target is in api_key mode).",
    )
    parser.add_argument(
        "-k",
        dest="keyword",
        default=None,
        help="Pytest -k expression to filter test names.",
    )
    parser.add_argument(
        "--junit",
        default=None,
        help="Write JUnit XML report to PATH.",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Less verbose output.")
    args = parser.parse_args(argv)

    if args.base_url:
        os.environ["SCENEAPI_TEST_BASE_URL"] = args.base_url.rstrip("/")
    if args.api_key:
        os.environ["SCENEAPI_TEST_KEY"] = args.api_key

    here = Path(__file__).resolve().parent
    pytest_args = [str(here)]
    if args.keyword:
        pytest_args += ["-k", args.keyword]
    if args.junit:
        pytest_args += [f"--junitxml={args.junit}"]
    pytest_args += ["-q"] if args.quiet else ["-v"]

    import pytest

    return pytest.main(pytest_args)


if __name__ == "__main__":
    sys.exit(main())
