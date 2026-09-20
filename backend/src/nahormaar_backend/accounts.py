# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persistent accounts, opaque sessions, and single-use login attempts."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base, database_engine
from .models import Contributor


class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    discord_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    avatar: Mapped[str]
    profile_complete: Mapped[bool] = mapped_column(default=False)


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


ACCOUNT_TABLES = tuple(
    Base.metadata.tables[name] for name in ("accounts", "sessions", "login_attempts")
)


@dataclass(frozen=True, slots=True)
class Account:
    profile: Contributor
    discord_id: str
    profile_complete: bool

    @classmethod
    def from_row(cls, row: AccountRow) -> "Account":
        return cls(
            Contributor(row.id, row.name, row.avatar),
            row.discord_id,
            row.profile_complete,
        )


class Accounts:
    """Use short independent transactions; migrations belong to SQLiteStore."""

    def __init__(self, path: Path) -> None:
        self._engine = database_engine(path)

    def close(self) -> None:
        self._engine.dispose()

    def begin_login(
        self, state_hash: str, browser_hash: str, verifier: str, now: float
    ) -> bool:
        with Session(self._engine) as session, session.begin():
            session.execute(delete(LoginRow).where(LoginRow.expires_at <= now))
            session.execute(
                delete(LoginRow).where(LoginRow.browser_hash == browser_hash)
            )
            if (
                session.execute(select(func.count()).select_from(LoginRow)).scalar_one()
                >= 512
            ):
                return False
            session.add(
                LoginRow(
                    state_hash=state_hash,
                    browser_hash=browser_hash,
                    verifier=verifier,
                    expires_at=now + 600,
                )
            )
            return True

    def consume_login(
        self, state_hash: str, browser_hash: str, now: float
    ) -> str | None:
        # DELETE RETURNING consumes the attempt atomically, including concurrent callbacks.
        with Session(self._engine) as session, session.begin():
            return session.scalar(
                delete(LoginRow)
                .where(
                    LoginRow.state_hash == state_hash,
                    LoginRow.browser_hash == browser_hash,
                    LoginRow.expires_at > now,
                )
                .returning(LoginRow.verifier)
            )

    def create_session(
        self,
        discord_id: str,
        name: str,
        avatar: str,
        token_hash: str,
        expires_at: float,
        now: float,
        previous_hash: str | None,
    ) -> Account:
        with Session(self._engine) as session, session.begin():
            session.execute(delete(SessionRow).where(SessionRow.expires_at <= now))
            if previous_hash is not None:
                session.execute(
                    delete(SessionRow).where(SessionRow.token_hash == previous_hash)
                )
            row = session.scalar(
                select(AccountRow).where(AccountRow.discord_id == discord_id)
            )
            if row is None:
                row = AccountRow(
                    id=uuid4(),
                    discord_id=discord_id,
                    name=name,
                    avatar=avatar,
                    profile_complete=False,
                )
                session.add(row)
                session.flush()
            session.add(
                SessionRow(
                    token_hash=token_hash, account_id=row.id, expires_at=expires_at
                )
            )
            return Account.from_row(row)

    def session(self, token_hash: str, now: float) -> tuple[Account, float] | None:
        with Session(self._engine) as session:
            result = session.execute(
                select(AccountRow, SessionRow.expires_at)
                .join(SessionRow, SessionRow.account_id == AccountRow.id)
                .where(SessionRow.token_hash == token_hash, SessionRow.expires_at > now)
            ).one_or_none()
            if result is None:
                return None
            return Account.from_row(result[0]), result[1]

    def logout(self, token_hash: str) -> None:
        with Session(self._engine) as session, session.begin():
            session.execute(
                delete(SessionRow).where(SessionRow.token_hash == token_hash)
            )

    def revoke(self, account_id: UUID) -> None:
        with Session(self._engine) as session, session.begin():
            session.execute(
                delete(SessionRow).where(SessionRow.account_id == account_id)
            )

    def update_profile(self, account_id: UUID, name: str, avatar: str) -> Account:
        with Session(self._engine) as session, session.begin():
            row = session.get(AccountRow, account_id)
            if row is None:
                raise ValueError("Account no longer exists.")
            row.name, row.avatar, row.profile_complete = name, avatar, True
            return Account.from_row(row)
