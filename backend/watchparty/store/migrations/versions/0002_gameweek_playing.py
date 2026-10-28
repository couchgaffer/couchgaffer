"""a gameweek being played by a member's session (#178)

Revision ID: 0002_gameweek_playing
Revises: 0001_create_leagues
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_gameweek_playing"
down_revision: str | None = "0001_create_leagues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gameweeks", sa.Column("playing_by", sa.String(40), nullable=True))
    op.add_column("gameweeks", sa.Column("playing_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gameweeks", sa.Column("playing_started", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("gameweeks", sa.Column("kicked_off_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gameweeks", sa.Column("cooldown_by", sa.String(40), nullable=True))
    op.add_column("gameweeks", sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("gameweeks", "cooldown_until")
    op.drop_column("gameweeks", "cooldown_by")
    op.drop_column("gameweeks", "kicked_off_at")
    op.drop_column("gameweeks", "playing_started")
    op.drop_column("gameweeks", "playing_until")
    op.drop_column("gameweeks", "playing_by")
