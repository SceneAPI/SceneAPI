"""sceneapi benchmark harness.

The harness drives a live server through the supported generated SDK
(``sceneapi_client_gen``), which ships in the sibling ``sfmapi-sdk``
repo and is not installed into this venv (its editable install is not
usable from source — see ``tests/contract/test_d_*``). Mirror the
contract tests instead: wire ``<sdk-repo>/python`` onto ``sys.path``
so ``import sceneapi_client_gen`` resolves from the checkout. Override
the SDK repo location with ``SFMAPI_SDK_REPO``.

Only ``sys.path`` is touched here — nothing from the SDK is imported
at package-import time, so SDK-free commands (``lint`` / ``history`` /
``plugins``) and the bench store keep working without the checkout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
_SDK_ROOT = Path(os.environ.get("SFMAPI_SDK_REPO", _SERVER_ROOT.parent / "sfmapi-sdk"))
_GEN_PARENT = _SDK_ROOT / "python"

if (_GEN_PARENT / "sceneapi_client_gen").is_dir() and str(_GEN_PARENT) not in sys.path:
    sys.path.insert(0, str(_GEN_PARENT))
