"""JSONL append-only result store + history queries + regression lint.

One JSONL file per git sha under `results/<sha>.jsonl`. Each line is a
single `BenchResult`. The lint compares the latest run for a given
`(dataset, recipe)` against the rolling median of the previous N runs
across all shas.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class BenchResult:
    dataset: str
    recipe: str
    git_sha: str
    runtime_version_id: str
    started_at: str
    finished_at: str
    wall_seconds: float
    status: str
    metrics: dict[str, float] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append(result: BenchResult) -> Path:
    """Append a result to `results/<git_sha>.jsonl`."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    target = RESULTS_DIR / f"{result.git_sha or 'local'}.jsonl"
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(result), sort_keys=True) + "\n")
    return target


def iter_history(limit_per_file: int | None = None) -> Iterator[BenchResult]:
    """Yield every recorded result, oldest-file first."""
    if not RESULTS_DIR.exists():
        return
    files = sorted(RESULTS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    for f in files:
        with f.open(encoding="utf-8") as fh:
            count = 0
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield BenchResult(**obj)
                count += 1
                if limit_per_file is not None and count >= limit_per_file:
                    break


def latest_per_combo(history: Iterable[BenchResult]) -> dict[tuple[str, str], BenchResult]:
    out: dict[tuple[str, str], BenchResult] = {}
    for r in history:
        out[(r.dataset, r.recipe)] = r
    return out


# Direction: "max" (higher better), "min" (lower better), default tol.
METRIC_DIRECTIONS: dict[str, tuple[str, float]] = {
    "num_reg_images": ("max", 0.05),
    "num_points3D": ("max", 0.05),
    "mean_reproj_err": ("min", 0.05),
    "wall_seconds": ("min", 0.25),
}


@dataclass
class Regression:
    dataset: str
    recipe: str
    metric: str
    direction: str
    baseline: float
    current: float
    pct_change: float
    tolerance: float

    def as_text(self) -> str:
        sign = "+" if self.pct_change >= 0 else ""
        return (
            f"{self.dataset}/{self.recipe}.{self.metric}: "
            f"baseline={self.baseline:.4g} current={self.current:.4g} "
            f"({sign}{self.pct_change * 100:.1f}% / tol={self.tolerance * 100:.0f}%)"
        )


def lint(
    results: Iterable[BenchResult],
    *,
    history_window: int = 10,
    tolerance_override: float | None = None,
) -> list[Regression]:
    """For each `(dataset, recipe)` combo represented in `results`, find
    the rolling median across the previous `history_window` historical
    rows of the SAME combo, then flag any metric whose change exceeds
    its tolerance in the wrong direction."""
    history = list(iter_history())
    by_combo: dict[tuple[str, str], list[BenchResult]] = {}
    for r in history:
        by_combo.setdefault((r.dataset, r.recipe), []).append(r)

    regressions: list[Regression] = []
    for cur in results:
        key = (cur.dataset, cur.recipe)
        all_for_combo = by_combo.get(key, [])
        # Exclude the current row itself (matched by exact identity if
        # it's already been appended; otherwise no overlap).
        prior = [r for r in all_for_combo if r is not cur][-history_window:]
        if len(prior) < 3:
            # Not enough history to call a regression yet.
            continue
        for metric, (direction, default_tol) in METRIC_DIRECTIONS.items():
            cur_val = cur.metrics.get(metric)
            if cur_val is None:
                continue
            samples = [r.metrics[metric] for r in prior if metric in r.metrics]
            if len(samples) < 3:
                continue
            base = statistics.median(samples)
            if base == 0:
                continue
            tol = default_tol if tolerance_override is None else tolerance_override
            pct = (cur_val - base) / base
            if direction == "max" and pct < -tol:
                regressions.append(
                    Regression(cur.dataset, cur.recipe, metric, direction, base, cur_val, pct, tol)
                )
            elif direction == "min" and pct > tol:
                regressions.append(
                    Regression(cur.dataset, cur.recipe, metric, direction, base, cur_val, pct, tol)
                )
    return regressions
