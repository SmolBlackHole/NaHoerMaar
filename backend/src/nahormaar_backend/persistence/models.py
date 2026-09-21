# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""SQLAlchemy mappings for the account and login tables."""

from uuid import UUID

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    discord_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    avatar: Mapped[str]
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
