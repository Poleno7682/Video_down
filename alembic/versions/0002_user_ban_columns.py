"""Ensure users.is_banned and users.banned_until exist (self-heal stale schema)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-20 00:00:00.000000

Some early installations created the ``users`` table (via create_all or an older
migration) without the ``is_banned`` / ``banned_until`` columns, then got stamped
at 0001. This migration adds the columns idempotently so such databases self-heal.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_until timestamptz"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS banned_until")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_banned")
