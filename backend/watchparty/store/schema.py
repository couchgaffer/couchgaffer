"""The tables. Migrations in migrations/versions are written by hand from these and checked against them
(test_migrations_apply_from_scratch_and_match_the_models in tests/test_store_db.py fails on any difference)."""

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Constraint names are fixed, so a migration and the model always agree on them.
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    # Null for the demo league, which nobody joins: it is read without a token.
    join_code: Mapped[str | None] = mapped_column(String(6), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = created()


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("league_id", "seat", name="uq_members_league_seat"),
        CheckConstraint("seat BETWEEN 1 AND 5", name="seat"),
        CheckConstraint("lang IN ('en', 'es')", name="lang"),
        CheckConstraint("style IN ('analyst', 'casual')", name="style"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    seat: Mapped[int] = mapped_column(SmallInteger)
    display_name: Mapped[str] = mapped_column(String(20))
    lang: Mapped[str] = mapped_column(String(2))
    style: Mapped[str] = mapped_column(String(8))
    favourite_club: Mapped[str | None] = mapped_column(String(40))
    favourite_player: Mapped[str | None] = mapped_column(String(40))
    is_bot: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # SHA-256 of the member token; the token itself is only ever in the member's browser.
    token_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = created()


class Gameweek(Base):
    __tablename__ = "gameweeks"
    __table_args__ = (CheckConstraint("status IN ('open', 'finished')", name="status"),)

    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed: Mapped[int] = mapped_column(Integer)  # the simulator seed: which match (or matches, #94) it plays
    status: Mapped[str] = mapped_column(String(8), server_default="open")
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A member's session holds it (#178): "<member id>/<nonce>", until when, and whether its match has started. A
    # claim whose match has not started lasts 45 s and any other member's session takes it over; a started one keeps
    # everyone else out until it is recorded, released (abandoned or failed) or runs out.
    playing_by: Mapped[str | None] = mapped_column(String(40))
    playing_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    playing_started: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # When a session's match first started: squads are fixed from then until the gameweek is recorded
    kicked_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The member whose session was last abandoned or never opened, and until when they cannot start another
    cooldown_by: Mapped[str | None] = mapped_column(String(40))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Squad(Base):
    __tablename__ = "squads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["league_id", "gameweek"],
            ["gameweeks.league_id", "gameweeks.number"],
            name="fk_squads_gameweek",
            ondelete="CASCADE",
        ),
        CheckConstraint("cardinality(players) = 5", name="size"),
        CheckConstraint("captain = ANY(players)", name="captain"),
    )

    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    gameweek: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[str] = mapped_column(String(40))
    players: Mapped[list[str]] = mapped_column(ARRAY(Text))
    captain: Mapped[str] = mapped_column(Text)
    picked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["league_id", "gameweek"],
            ["gameweeks.league_id", "gameweeks.number"],
            name="fk_results_gameweek",
            ondelete="CASCADE",
        ),
        CheckConstraint("rank BETWEEN 1 AND 5", name="rank"),
    )

    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    gameweek: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[str] = mapped_column(String(40), index=True)
    points: Mapped[float] = mapped_column(Double)
    rank: Mapped[int] = mapped_column(SmallInteger)
    expected_goals: Mapped[float] = mapped_column(Double)
    goals: Mapped[int] = mapped_column(Integer)
    recap_url: Mapped[str | None] = mapped_column(Text)  # set by the recap job once the video exists (#15)
