from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    display_name: Mapped[str | None] = mapped_column(String)
    avatar: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class League(Base):
    __tablename__ = "leagues"
    league_id: Mapped[str] = mapped_column(String, primary_key=True)
    platform: Mapped[str] = mapped_column(String, default="sleeper", index=True)
    name: Mapped[str] = mapped_column(String)
    season: Mapped[str] = mapped_column(String, index=True)
    total_rosters: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str | None] = mapped_column(String)
    season_type: Mapped[str | None] = mapped_column(String)
    draft_id: Mapped[str | None] = mapped_column(String)
    previous_league_id: Mapped[str | None] = mapped_column(String)
    roster_positions: Mapped[list[str]] = mapped_column(JSON, default=list)
    scoring_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class LeagueUser(Base):
    __tablename__ = "league_users"
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.league_id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String)
    team_name: Mapped[str | None] = mapped_column(String)
    is_commissioner: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FantasyRoster(Base):
    __tablename__ = "fantasy_rosters"
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.league_id", ondelete="CASCADE"), primary_key=True)
    roster_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String, index=True)
    co_owner_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_primary_user: Mapped[bool] = mapped_column(Boolean, default=False)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    ties: Mapped[int] = mapped_column(Integer, default=0)
    points_for: Mapped[float] = mapped_column(Float, default=0)
    points_against: Mapped[float] = mapped_column(Float, default=0)
    waiver_position: Mapped[int | None] = mapped_column(Integer)
    waiver_budget_used: Mapped[int | None] = mapped_column(Integer)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    players: Mapped[list["RosterPlayer"]] = relationship(cascade="all, delete-orphan")


class NFLPlayer(Base):
    __tablename__ = "nfl_players"
    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String, index=True)
    first_name: Mapped[str | None] = mapped_column(String)
    last_name: Mapped[str | None] = mapped_column(String)
    team: Mapped[str | None] = mapped_column(String, index=True)
    position: Mapped[str | None] = mapped_column(String, index=True)
    fantasy_positions: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str | None] = mapped_column(String)
    injury_status: Mapped[str | None] = mapped_column(String)
    depth_chart_position: Mapped[str | None] = mapped_column(String)
    depth_chart_order: Mapped[int | None] = mapped_column(Integer)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class RosterPlayer(Base):
    __tablename__ = "roster_players"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(String)
    roster_id: Mapped[int] = mapped_column(Integer)
    player_id: Mapped[str] = mapped_column(ForeignKey("nfl_players.player_id"))
    is_starter: Mapped[bool] = mapped_column(Boolean, default=False)
    is_reserve: Mapped[bool] = mapped_column(Boolean, default=False)
    is_taxi: Mapped[bool] = mapped_column(Boolean, default=False)
    slot: Mapped[str | None] = mapped_column(String)
    __table_args__ = (
        ForeignKeyConstraint(["league_id", "roster_id"], ["fantasy_rosters.league_id", "fantasy_rosters.roster_id"], ondelete="CASCADE"),
        UniqueConstraint("league_id", "roster_id", "player_id"),
    )


class WeeklyMatchup(Base):
    __tablename__ = "weekly_matchups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[str] = mapped_column(String, index=True)
    week: Mapped[int] = mapped_column(Integer)
    matchup_id: Mapped[int | None] = mapped_column(Integer)
    roster_id: Mapped[int] = mapped_column(Integer)
    points: Mapped[float] = mapped_column(Float, default=0)
    starters: Mapped[list[str]] = mapped_column(JSON, default=list)
    players: Mapped[list[str]] = mapped_column(JSON, default=list)
    player_points: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("league_id", "week", "roster_id"),)


class JsonRecordMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[str] = mapped_column(String, index=True)
    week: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WeeklyPlayerProjection(JsonRecordMixin, Base): __tablename__ = "weekly_player_projections"
class WeeklyPlayerMetric(JsonRecordMixin, Base): __tablename__ = "weekly_player_metrics"
class Transaction(JsonRecordMixin, Base): __tablename__ = "transactions"
class PlayoffBracket(JsonRecordMixin, Base): __tablename__ = "playoff_brackets"
class LineupRecommendation(JsonRecordMixin, Base): __tablename__ = "lineup_recommendations"
class WaiverRecommendation(JsonRecordMixin, Base): __tablename__ = "waiver_recommendations"
class RecommendationResult(JsonRecordMixin, Base): __tablename__ = "recommendation_results"


class SyncHistory(Base):
    __tablename__ = "sync_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String)
    season: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String)
    leagues_synced: Mapped[int] = mapped_column(Integer, default=0)
    rosters_synced: Mapped[int] = mapped_column(Integer, default=0)
    players_synced: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"
    platform: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str | None] = mapped_column(String)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
