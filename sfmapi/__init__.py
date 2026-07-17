"""Public Python API for sfmapi extensions.

Backend packages should import from this package's facade modules
(:mod:`sfmapi.runtime`, :mod:`sfmapi.backends`, ...) instead of the
server's internal :mod:`sfmapi.server` package, which remains the
implementation detail that powers the FastAPI service.
"""

from sfmapi.server import __version__

__all__ = ["__version__"]
