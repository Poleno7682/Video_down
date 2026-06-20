"""Initial schema with FK constraint on download_requests.video_id

Revision ID: 0001
Revises:
Create Date: 2026-06-20 00:00:00.000000

NOTE FOR EXISTING INSTALLATIONS (created via create_all before Alembic):
  Run:  alembic stamp 0001
  This marks the migration as applied without re-running DDL. The FK constraint
  will then be added by checking for its existence at runtime.

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    # Ensure PostgreSQL enum types exist before creating tables.
    existing_enums = {
        row[0]
        for row in bind.execute(sa.text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
    }

    if "downloadstatus" not in existing_enums:
        sa.Enum(
            "queued", "downloading", "sending", "done", "failed", "too_large", "rate_limited",
            name="downloadstatus",
        ).create(bind)

    if "telegramfiletype" not in existing_enums:
        sa.Enum("video", "document", "audio", name="telegramfiletype").create(bind)

    download_status = postgresql.ENUM(
        "queued", "downloading", "sending", "done", "failed", "too_large", "rate_limited",
        name="downloadstatus",
        create_type=False,
    )
    telegram_file_type = postgresql.ENUM(
        "video", "document", "audio",
        name="telegramfiletype",
        create_type=False,
    )

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("username", sa.String(255), nullable=True),
            sa.Column("first_name", sa.String(255), nullable=True),
            sa.Column("is_banned", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("banned_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "videos" not in existing_tables:
        op.create_table(
            "videos",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("original_url", sa.Text(), nullable=False),
            sa.Column("normalized_url", sa.Text(), nullable=False),
            sa.Column("url_hash", sa.String(64), nullable=False),
            sa.Column("quality", sa.String(32), nullable=False),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("telegram_file_id", sa.Text(), nullable=True),
            sa.Column("telegram_file_unique_id", sa.Text(), nullable=True),
            sa.Column("telegram_file_type", telegram_file_type, nullable=True),
            sa.Column("local_file_path", sa.Text(), nullable=True),
            sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("is_ready", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("url_hash", "quality", name="uq_video_url_quality"),
        )
        op.create_index("idx_video_url_hash", "videos", ["url_hash"])

    if "download_requests" not in existing_tables:
        op.create_table(
            "download_requests",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", sa.BigInteger(), nullable=True),
            sa.Column("status_message_id", sa.BigInteger(), nullable=True),
            sa.Column("video_id", sa.Integer(), nullable=True),
            sa.Column("original_url", sa.Text(), nullable=False),
            sa.Column("normalized_url", sa.Text(), nullable=False),
            sa.Column("url_hash", sa.String(64), nullable=False),
            sa.Column("quality", sa.String(32), nullable=False),
            sa.Column("status", download_status, nullable=False, server_default="queued"),
            sa.Column("celery_task_id", sa.String(255), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], name="fk_dr_video_id", ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_request_user_status", "download_requests", ["user_id", "status"])
        op.create_index("idx_request_video_status", "download_requests", ["video_id", "status"])
    else:
        # Table already exists from a previous create_all() run.
        # Add the FK constraint if it is missing.
        existing_fks = {fk["name"] for fk in insp.get_foreign_keys("download_requests")}
        if "fk_dr_video_id" not in existing_fks:
            op.create_foreign_key(
                "fk_dr_video_id",
                "download_requests",
                "videos",
                ["video_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    if "download_requests" in existing_tables:
        op.drop_table("download_requests")
    if "videos" in existing_tables:
        op.drop_table("videos")
    if "users" in existing_tables:
        op.drop_table("users")

    bind.execute(sa.text("DROP TYPE IF EXISTS downloadstatus"))
    bind.execute(sa.text("DROP TYPE IF EXISTS telegramfiletype"))
