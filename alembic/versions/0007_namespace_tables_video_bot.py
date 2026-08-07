"""Namespace all project tables with a _video_bot suffix

The Postgres instance this project's DB lives on is shared with at least one
other, unrelated project (see docs/db-table-namespacing.md). A real collision
already happened on a neighboring bot in this same setup: its `users` table
matched a different project's `users` table under a completely different
column layout, and `Base.metadata.create_all()` silently skipped creating the
real one because a table with that name already existed. To make this
project's tables structurally immune to that failure mode, every one of them
gets a `_video_bot` suffix (matching the existing `video_bot_*` container
naming already used in docker-compose*.yml).

Verified against the real shared database before writing this migration
(read-only audit, see docs/db-table-namespacing.md): all six of this
project's tables (`users`, `videos`, `download_requests`, `user_cookies`,
`user_google_tokens`, `proxies`) are genuinely this project's own — their
columns match the models exactly, none of them collide with another
project's table of the same name there. The only FK in the whole database
between two of this project's own tables is
download_requests.video_id -> videos.id (fk_dr_video_id); Postgres resolves
FK constraints by the referenced table's OID, not its name, so renaming both
tables in place preserves that constraint automatically without recreating
it.

This migration still detects, per table and independently of the above,
whether a table under the old bare name is genuinely this project's own
(via a column that only this project's schema has) before deciding whether
to rename it in place or create the namespaced table fresh instead - so it
also behaves correctly on a clean/local database (nothing to rename, tables
simply get created) and on any other shared deployment where a real
collision *does* exist (the foreign table under the old name is left
completely untouched).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-22 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUFFIX = "_video_bot"

# One characteristic, distinctive column per table that only this project's
# schema would have - used to tell "this is genuinely our table" apart from
# "a different project's table that happens to share the bare name".
_CHARACTERISTIC_COLUMN = {
    "users": "is_banned",
    "videos": "url_hash",
    "download_requests": "celery_task_id",
    "user_cookies": "cookies_text",
    "user_google_tokens": "refresh_token",
    "proxies": "failure_count",
}


def _is_ours(bind, table: str) -> bool:
    # A fresh Inspector per check, not a shared/reused one - each rename in
    # this migration changes the catalog, and a reused Inspector instance
    # can otherwise serve a stale, cached get_table_names()/get_columns()
    # result from before that change.
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    columns = {col["name"] for col in inspector.get_columns(table)}
    return _CHARACTERISTIC_COLUMN[table] in columns


def _ensure_enum(bind, name: str, labels: list[str]) -> None:
    existing = {
        row[0] for row in bind.execute(sa.text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
    }
    if name not in existing:
        sa.Enum(*labels, name=name).create(bind)


def _pg_enum(name: str, labels: list[str], is_postgres: bool):
    if is_postgres:
        return postgresql.ENUM(*labels, name=name, create_type=False)
    return sa.Enum(*labels, name=name)


def _create_users_fresh(new_name: str) -> None:
    op.create_table(
        new_name,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("banned_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_videos_fresh(new_name: str, telegram_file_type) -> None:
    op.create_table(
        new_name,
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
    op.create_index(f"idx_video_url_hash{SUFFIX}", new_name, ["url_hash"])


def _create_download_requests_fresh(new_name: str, videos_table: str, download_status) -> None:
    op.create_table(
        new_name,
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
        sa.ForeignKeyConstraint(
            ["video_id"], [f"{videos_table}.id"], name="fk_dr_video_id", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"idx_request_user_status{SUFFIX}", new_name, ["user_id", "status"])
    op.create_index(f"idx_request_video_status{SUFFIX}", new_name, ["video_id", "status"])
    op.create_index(f"idx_request_status{SUFFIX}", new_name, ["status"])


def _create_user_cookies_fresh(new_name: str) -> None:
    op.create_table(
        new_name,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("cookies_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "platform", name="uq_user_cookies_user_platform"),
    )
    op.create_index(f"idx_user_cookies_user{SUFFIX}", new_name, ["user_id"])


def _create_user_google_tokens_fresh(new_name: str) -> None:
    op.create_table(
        new_name,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_google_token_user"),
    )


def _create_proxies_fresh(new_name: str) -> None:
    op.create_table(
        new_name,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", name="uq_proxy_url"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        _ensure_enum(
            bind,
            "downloadstatus",
            ["queued", "downloading", "sending", "done", "failed", "too_large", "rate_limited"],
        )
        _ensure_enum(bind, "telegramfiletype", ["video", "document", "audio"])
    download_status = _pg_enum(
        "downloadstatus",
        ["queued", "downloading", "sending", "done", "failed", "too_large", "rate_limited"],
        is_postgres,
    )
    telegram_file_type = _pg_enum("telegramfiletype", ["video", "document", "audio"], is_postgres)

    # Independent tables (no FK to any other project table) - rename in
    # place if the bare-named table is genuinely ours, otherwise leave the
    # foreign occupant untouched and create our own under the new name.
    if _is_ours(bind, "users"):
        op.rename_table("users", f"users{SUFFIX}")
    else:
        _create_users_fresh(f"users{SUFFIX}")

    if _is_ours(bind, "user_cookies"):
        op.rename_table("user_cookies", f"user_cookies{SUFFIX}")
    else:
        _create_user_cookies_fresh(f"user_cookies{SUFFIX}")

    if _is_ours(bind, "user_google_tokens"):
        op.rename_table("user_google_tokens", f"user_google_tokens{SUFFIX}")
    else:
        _create_user_google_tokens_fresh(f"user_google_tokens{SUFFIX}")

    if _is_ours(bind, "proxies"):
        op.rename_table("proxies", f"proxies{SUFFIX}")
    else:
        _create_proxies_fresh(f"proxies{SUFFIX}")

    # videos must be namespaced before download_requests, which FKs into it.
    if _is_ours(bind, "videos"):
        op.rename_table("videos", f"videos{SUFFIX}")
    else:
        _create_videos_fresh(f"videos{SUFFIX}", telegram_file_type)

    if _is_ours(bind, "download_requests"):
        # download_requests was genuinely ours, and so - by the same
        # verified audit - was videos: fk_dr_video_id is resolved by OID,
        # so it now transparently points at videos_video_bot without any
        # extra action here.
        op.rename_table("download_requests", f"download_requests{SUFFIX}")
    else:
        _create_download_requests_fresh(
            f"download_requests{SUFFIX}", f"videos{SUFFIX}", download_status
        )


def _bare_name_still_occupied(bind, table: str) -> bool:
    # Fresh Inspector per check - see the comment in _is_ours() for why a
    # shared/reused Inspector instance is avoided across catalog changes.
    return table in sa.inspect(bind).get_table_names()


def downgrade() -> None:
    bind = op.get_bind()

    # download_requests first: it FKs into videos, so it must be gone
    # (dropped or renamed back) before videos is touched.
    if _bare_name_still_occupied(bind, "download_requests"):
        # Bare name still occupied by a foreign table -> ours was created
        # fresh under the suffixed name; drop it (indexes/FK go with it).
        op.drop_table(f"download_requests{SUFFIX}")
    else:
        op.rename_table(f"download_requests{SUFFIX}", "download_requests")

    if _bare_name_still_occupied(bind, "videos"):
        op.drop_table(f"videos{SUFFIX}")
    else:
        op.rename_table(f"videos{SUFFIX}", "videos")

    if _bare_name_still_occupied(bind, "proxies"):
        op.drop_table(f"proxies{SUFFIX}")
    else:
        op.rename_table(f"proxies{SUFFIX}", "proxies")

    if _bare_name_still_occupied(bind, "user_google_tokens"):
        op.drop_table(f"user_google_tokens{SUFFIX}")
    else:
        op.rename_table(f"user_google_tokens{SUFFIX}", "user_google_tokens")

    if _bare_name_still_occupied(bind, "user_cookies"):
        op.drop_table(f"user_cookies{SUFFIX}")
    else:
        op.rename_table(f"user_cookies{SUFFIX}", "user_cookies")

    if _bare_name_still_occupied(bind, "users"):
        op.drop_table(f"users{SUFFIX}")
    else:
        op.rename_table(f"users{SUFFIX}", "users")
