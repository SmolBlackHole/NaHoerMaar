# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persistent accounts, roles, sessions, and single-use login attempts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..domain.access import AccessEvent, AccessGrant, AccessRole
from ..domain.accounts import Account
from ..domain.identity import Contributor
from ..domain.preferences import Appearance
from .database import database_engine
from .models import AccessEventRow, AccountRow, LoginRow, SessionRow


def account_from_row(row: AccountRow) -> Account:
    if row.name is None or row.avatar is None:
        raise ValueError("Account profile has not been initialized.")
    return Account(
        Contributor(row.id, row.name, row.avatar),
        row.discord_id,
        AccessRole(row.role) if row.role is not None else None,
        row.access_granted_by,
        row.access_granted_at,
        row.profile_complete,
        Appearance.model_validate(row.appearance),
    )


def access_event_from_row(row: AccessEventRow) -> AccessEvent:
    return AccessEvent(
        row.id, row.action, row.discord_id, row.actor_id, row.occurred_at
    )


class Accounts:
    """Use short independent transactions on the schema initialized at startup."""

    def __init__(self, database: str) -> None:
        self._engine = database_engine(database)

    def close(self) -> None:
        self._engine.dispose()

    def access_role(self, discord_id: str) -> AccessRole | None:
        with Session(self._engine) as session:
            value = session.scalar(
                select(AccountRow.role).where(AccountRow.discord_id == discord_id)
            )
            return AccessRole(value) if value is not None else None

    def account(self, discord_id: str) -> Account | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(AccountRow).where(AccountRow.discord_id == discord_id)
            )
            if row is None or row.name is None or row.avatar is None:
                return None
            return account_from_row(row)

    def access_grants(self) -> tuple[AccessGrant, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(AccountRow)
                .where(AccountRow.role == AccessRole.USER.value)
                .order_by(AccountRow.access_granted_at, AccountRow.discord_id)
            )
            grants: list[AccessGrant] = []
            for row in rows:
                if row.access_granted_by is None or row.access_granted_at is None:
                    raise ValueError("Listener access is missing its grant metadata.")
                grants.append(
                    AccessGrant(
                        row.discord_id,
                        row.access_granted_by,
                        row.access_granted_at,
                        row.name,
                    )
                )
            return tuple(grants)

    def access_history(self, limit: int = 100) -> tuple[AccessEvent, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(AccessEventRow)
                .order_by(AccessEventRow.occurred_at.desc(), AccessEventRow.id.desc())
                .limit(limit)
            )
            return tuple(access_event_from_row(row) for row in rows)

    def grant_access(
        self, discord_id: str, actor_id: str, occurred_at: datetime
    ) -> bool:
        with Session(self._engine) as session, session.begin():
            changed = session.scalar(
                insert(AccountRow)
                .values(
                    id=uuid4(),
                    discord_id=discord_id,
                    name=None,
                    avatar=None,
                    role=AccessRole.USER.value,
                    access_granted_by=actor_id,
                    access_granted_at=occurred_at,
                    profile_complete=False,
                    appearance={},
                )
                .on_conflict_do_update(
                    index_elements=[AccountRow.discord_id],
                    set_={
                        AccountRow.role: AccessRole.USER.value,
                        AccountRow.access_granted_by: actor_id,
                        AccountRow.access_granted_at: occurred_at,
                    },
                    where=AccountRow.role.is_distinct_from(AccessRole.USER.value),
                )
                .returning(AccountRow.discord_id)
            )
            if changed is None:
                return False
            session.add(
                AccessEventRow(
                    id=uuid4(),
                    action="granted",
                    discord_id=discord_id,
                    actor_id=actor_id,
                    occurred_at=occurred_at,
                )
            )
            return True

    def revoke_access(
        self,
        discord_id: str,
        actor_id: str,
        occurred_at: datetime,
        *,
        owner: bool,
    ) -> bool:
        with Session(self._engine) as session, session.begin():
            row = session.scalar(
                select(AccountRow)
                .where(AccountRow.discord_id == discord_id)
                .with_for_update()
            )
            if row is None or row.role != AccessRole.USER.value:
                return False
            if not owner and row.access_granted_by != actor_id:
                raise PermissionError("Admins may revoke only access they granted.")
            session.execute(delete(SessionRow).where(SessionRow.account_id == row.id))
            row.role = None
            row.access_granted_by = None
            row.access_granted_at = None
            session.add(
                AccessEventRow(
                    id=uuid4(),
                    action="revoked",
                    discord_id=discord_id,
                    actor_id=actor_id,
                    occurred_at=occurred_at,
                )
            )
            return True

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
        role: AccessRole,
        token_hash: str,
        expires_at: float,
        now: float,
        previous_hash: str | None,
    ) -> Account | None:
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
                if role is AccessRole.USER:
                    return None
                row = AccountRow(
                    id=uuid4(),
                    discord_id=discord_id,
                    name=name,
                    avatar=avatar,
                    role=role.value,
                    access_granted_by=None,
                    access_granted_at=None,
                    profile_complete=False,
                )
                session.add(row)
                session.flush()
            else:
                if role is AccessRole.USER and row.role != AccessRole.USER.value:
                    return None
                if role.privileged:
                    row.role = role.value
                    row.access_granted_by = None
                    row.access_granted_at = None
                if row.name is None or row.avatar is None:
                    row.name, row.avatar = name, avatar
            session.add(
                SessionRow(
                    token_hash=token_hash, account_id=row.id, expires_at=expires_at
                )
            )
            return account_from_row(row)

    def session(self, token_hash: str, now: float) -> tuple[Account, float] | None:
        with Session(self._engine) as session:
            result = session.execute(
                select(AccountRow, SessionRow.expires_at)
                .join(SessionRow, SessionRow.account_id == AccountRow.id)
                .where(SessionRow.token_hash == token_hash, SessionRow.expires_at > now)
            ).one_or_none()
            if result is None:
                return None
            return account_from_row(result[0]), result[1]

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
            return account_from_row(row)

    def update_appearance(self, account_id: UUID, appearance: Appearance) -> Appearance:
        with Session(self._engine) as session, session.begin():
            row = session.get(AccountRow, account_id)
            if row is None:
                raise ValueError("Account no longer exists.")
            row.appearance = appearance.model_dump()
            return appearance
