from enum import Enum


class TaskProgressOutStatus(str, Enum):
    CANCELLED = "cancelled"
    CANCELLED_DIRTY = "cancelled_dirty"
    FAILED = "failed"
    PENDING = "pending"
    RUNNING = "running"
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"

    def __str__(self) -> str:
        return str(self.value)
