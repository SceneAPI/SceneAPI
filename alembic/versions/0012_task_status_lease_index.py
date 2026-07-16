"""add composite (status, lease_expires_at) index on task

The janitor's hot predicates — lease reclaim (``status='running' AND
lease_expires_at < now``) and the pending-task dependency sweeps
(``status='pending'``) — get a single composite index. The existing
single-column ``ix_task_status`` / ``ix_task_lease_expires_at`` indexes
are kept; dropping them is a separate decision.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-16

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_task_status_lease", "task", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_task_status_lease", table_name="task")
