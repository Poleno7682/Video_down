"""Add user_cookies table for per-user yt-dlp cookies

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-20 00:00:00.000000

Stores per-user cookies (Netscape format) per platform so the bot and worker
containers share one source of truth. Created idempotently so databases that
already have the table (e.g. via create_all) self-heal.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "user_cookies" in set(insp.get_table_names()):
        return

    op.create_table(
        "user_cookies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("cookies_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "platform", name="uq_user_cookies_user_platform"),
    )
    op.create_index("idx_user_cookies_user", "user_cookies", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "user_cookies" in set(insp.get_table_names()):
        op.drop_table("user_cookies")
