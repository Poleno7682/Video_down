"""Add proxies table for SOCKS5(h) proxy pool with failover

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-19 00:00:00.000000

Lets admins manage a pool of yt-dlp proxies via Telegram commands instead of
a single static YTDLP_PROXY env var — the worker rotates through them
(least-failed first) and falls back to the next one when a request fails.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "proxies" in set(insp.get_table_names()):
        return

    op.create_table(
        "proxies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", name="uq_proxy_url"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "proxies" in set(insp.get_table_names()):
        op.drop_table("proxies")
