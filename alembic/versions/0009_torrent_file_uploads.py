"""Allow torrent_requests_video_bot rows to come from an uploaded .torrent file

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-05 00:00:00.000000

A magnet link isn't the only way to start a torrent download - a user can
also upload the .torrent file itself, which already carries the metadata a
magnet link would otherwise need the swarm/DHT to hand back. magnet_uri
becomes nullable and a new torrent_file column holds the raw upload; exactly
one of the two is set per row (enforced in app code, see
app.worker.tasks.process_torrent_request).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("torrent_requests_video_bot")}

    if "torrent_file" not in columns:
        op.add_column(
            "torrent_requests_video_bot",
            sa.Column("torrent_file", sa.LargeBinary(), nullable=True),
        )
    op.alter_column(
        "torrent_requests_video_bot", "magnet_uri",
        existing_type=sa.Text(), nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("torrent_requests_video_bot")}

    op.alter_column(
        "torrent_requests_video_bot", "magnet_uri",
        existing_type=sa.Text(), nullable=False,
    )
    if "torrent_file" in columns:
        op.drop_column("torrent_requests_video_bot", "torrent_file")
