from enum import Enum


class BackendActionOutStability(str, Enum):
    BACKEND_EXTENSION = "backend_extension"
    DEPRECATED = "deprecated"
    EXPERIMENTAL = "experimental"
    STABLE = "stable"

    def __str__(self) -> str:
        return str(self.value)
