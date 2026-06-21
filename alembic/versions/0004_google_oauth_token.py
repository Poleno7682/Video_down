"""Add user_google_tokens table for Google OAuth2 refresh tokens

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-21 00:00:00.000000

Stores per-user Google OAuth2 refresh tokens so the bot can automatically
renew YouTube session cookies when they expire.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "user_google_tokens" in set(insp.get_table_names()):
        return

    op.create_table(
        "user_google_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_google_token_user"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "user_google_tokens" in set(insp.get_table_names()):
        op.drop_table("user_google_tokens")
