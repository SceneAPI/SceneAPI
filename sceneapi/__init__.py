"""Public Python API for sfmapi extensions.

Backend packages should import from this package's facade modules
(:mod:`sceneapi.runtime`, :mod:`sceneapi.backends`, ...) instead of the
server's internal :mod:`sceneapi.server` package, which remains the
implementation detail that powers the FastAPI service.
"""

from sceneapi.server import __version__

__all__ = ["__version__"]
