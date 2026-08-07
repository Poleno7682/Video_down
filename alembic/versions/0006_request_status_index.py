"""Add index on download_requests.status for global active-request counts

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-21 00:00:00.000000

count_global_active_requests() filters download_requests by status alone
(no user_id/video_id predicate), so neither idx_request_user_status nor
idx_request_video_status can be used to satisfy it — every call to that
method (fired on each incoming link and each /status) was a full table
scan on a table that only grows over time. Adds a plain index on status.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_request_status ON download_requests (status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_request_status")
