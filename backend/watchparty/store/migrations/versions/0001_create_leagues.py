"""create leagues, members, gameweeks, squads and results (#96)

Revision ID: 0001_create_leagues
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_create_leagues"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "leagues",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("join_code", sa.String(6), nullable=True),
        sa.Column("is_demo", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_leagues"),
        sa.UniqueConstraint("join_code", name="uq_leagues_join_code"),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("league_id", sa.String(40), nullable=False),
        sa.Column("seat", sa.SmallInteger(), nullable=False),
        sa.Column("display_name", sa.String(20), nullable=False),
        sa.Column("lang", sa.String(2), nullable=False),
        sa.Column("style", sa.String(8), nullable=False),
        sa.Column("favourite_club", sa.String(40), nullable=True),
        sa.Column("favourite_player", sa.String(40), nullable=True),
        sa.Column("is_bot", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint("seat BETWEEN 1 AND 5", name="ck_members_seat"),
        sa.CheckConstraint("lang IN ('en', 'es')", name="ck_members_lang"),
        sa.CheckConstraint("style IN ('analyst', 'casual')", name="ck_members_style"),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], name="fk_members_league_id_leagues", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_members"),
        sa.UniqueConstraint("league_id", "seat", name="uq_members_league_seat"),
    )
    op.create_index("ix_members_league_id", "members", ["league_id"])
    op.create_table(
        "gameweeks",
        sa.Column("league_id", sa.String(40), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(8), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'finished')", name="ck_gameweeks_status"),
        sa.ForeignKeyConstraint(
            ["league_id"], ["leagues.id"], name="fk_gameweeks_league_id_leagues", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("league_id", "number", name="pk_gameweeks"),
    )
    op.create_table(
        "squads",
        sa.Column("member_id", sa.String(40), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.String(40), nullable=False),
        sa.Column("players", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("captain", sa.Text(), nullable=False),
        sa.Column("picked_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint("cardinality(players) = 5", name="ck_squads_size"),
        sa.CheckConstraint("captain = ANY(players)", name="ck_squads_captain"),
        sa.ForeignKeyConstraint(
            ["league_id", "gameweek"],
            ["gameweeks.league_id", "gameweeks.number"],
            name="fk_squads_gameweek",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_squads_member_id_members", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("member_id", "gameweek", name="pk_squads"),
    )
    op.create_table(
        "results",
        sa.Column("member_id", sa.String(40), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.String(40), nullable=False),
        sa.Column("points", sa.Double(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("expected_goals", sa.Double(), nullable=False),
        sa.Column("goals", sa.Integer(), nullable=False),
        sa.Column("recap_url", sa.Text(), nullable=True),
        sa.CheckConstraint("rank BETWEEN 1 AND 5", name="ck_results_rank"),
        sa.ForeignKeyConstraint(
            ["league_id", "gameweek"],
            ["gameweeks.league_id", "gameweeks.number"],
            name="fk_results_gameweek",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_results_member_id_members", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("member_id", "gameweek", name="pk_results"),
    )
    op.create_index("ix_results_league_id", "results", ["league_id"])


def downgrade() -> None:
    op.drop_table("results")
    op.drop_table("squads")
    op.drop_table("gameweeks")
    op.drop_index("ix_members_league_id", table_name="members")
    op.drop_table("members")
    op.drop_table("leagues")
