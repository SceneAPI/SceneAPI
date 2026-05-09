from enum import Enum


class BackendActionOutSideEffects(str, Enum):
    NONE = "none"
    READ = "read"
    UNKNOWN = "unknown"
    WRITE = "write"

    def __str__(self) -> str:
        return str(self.value)
