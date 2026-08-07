"""Add torrent_requests_video_bot for magnet-link downloads

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07 00:00:00.000000

Magnet downloads aren't "a video at a quality" - they're an arbitrary set of
files with no caching/reuse concept (the bot deletes everything right after
sending it), so this is a standalone table rather than reusing
videos_video_bot/download_requests_video_bot. See app.db.models.TorrentRequest
and app.worker.tasks.process_torrent_request.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The "downloadstatus" enum type already exists in this DB, created by
# download_requests_video_bot's own "status" column (see 0001_initial) -
# create_type=False so this migration doesn't try to CREATE TYPE it again.
download_status = postgresql.ENUM(
    "queued", "downloading", "sending", "done", "failed", "too_large", "rate_limited",
    name="downloadstatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "torrent_requests_video_bot" in set(insp.get_table_names()):
        return

    op.create_table(
        "torrent_requests_video_bot",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("status_message_id", sa.BigInteger(), nullable=True),
        sa.Column("magnet_uri", sa.Text(), nullable=False),
        sa.Column("info_hash", sa.String(length=64), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("files_sent", sa.Integer(), nullable=False),
        sa.Column("status", download_status, nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_torrent_request_user_status", "torrent_requests_video_bot", ["user_id", "status"],
    )
    op.create_index(
        "idx_torrent_request_status", "torrent_requests_video_bot", ["status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "torrent_requests_video_bot" in set(insp.get_table_names()):
        op.drop_table("torrent_requests_video_bot")
