from enum import Enum


class PairsSpecStrategy(str, Enum):
    EXHAUSTIVE = "exhaustive"
    EXPLICIT = "explicit"
    FROM_POSES = "from_poses"
    RETRIEVAL = "retrieval"
    SEQUENTIAL = "sequential"
    SPATIAL = "spatial"
    VOCABTREE = "vocabtree"

    def __str__(self) -> str:
        return str(self.value)
