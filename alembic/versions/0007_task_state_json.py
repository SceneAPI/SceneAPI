"""phase 7 add `task_state_json` on task

Splits the overloaded ``outputs_ref_json`` column into two:

- ``task_state_json`` (this migration): pre-execution carrier of
  ``inputs`` + ``spec`` for the worker.
- ``outputs_ref_json`` (existing): post-execution result reference
  written by the dispatcher.

See ``L27`` in ``docs/guides/decisions.md`` for the rationale —
the previous overload caused a 19-file ``__inputs``/``__spec``
magic-key duplication and made ``TaskOut.outputs_ref`` impossible
to type cleanly. Pre-release: no in-flight production data, so no
backfill needed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("task_state_json", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.drop_column("task_state_json")
