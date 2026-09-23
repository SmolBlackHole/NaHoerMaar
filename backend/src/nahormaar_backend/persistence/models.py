# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""SQLAlchemy mappings for accounts, roles, audit history and login state."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AccountRow(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "role IS NULL OR role IN ('owner','admin','user')",
            name="ck_accounts_role",
        ),
        CheckConstraint(
            "(role = 'user' AND access_granted_by IS NOT NULL "
            "AND access_granted_at IS NOT NULL) OR "
            "(role IS DISTINCT FROM 'user' AND access_granted_by IS NULL "
            "AND access_granted_at IS NULL)",
            name="ck_accounts_access_grant",
        ),
        Index("ix_accounts_access_granted_by", "access_granted_by"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    discord_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str | None]
    avatar: Mapped[str | None]
    role: Mapped[str | None]
    access_granted_by: Mapped[str | None]
    access_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_complete: Mapped[bool] = mapped_column(default=False)
    appearance: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, server_default="{}"
    )


class SessionRow(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    expires_at: Mapped[float]


class LoginRow(Base):
    __tablename__ = "login_attempts"

    state_hash: Mapped[str] = mapped_column(primary_key=True)
    browser_hash: Mapped[str] = mapped_column(unique=True)
    verifier: Mapped[str]
    expires_at: Mapped[float]


class AccessEventRow(Base):
    __tablename__ = "access_events"
    __table_args__ = (Index("ix_access_events_occurred_at", "occurred_at", "id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    action: Mapped[str]
    discord_id: Mapped[str] = mapped_column(index=True)
    actor_id: Mapped[str] = mapped_column(index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
