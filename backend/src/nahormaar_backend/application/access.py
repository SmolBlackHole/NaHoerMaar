# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Privileged operators, persistent listener grants and audit history."""

import asyncio
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ..domain.access import AccessEvent, AccessGrant, AccessRole
from ..domain.identity import AuthError, discord_id
from ..persistence.accounts import Accounts

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Operators:
    owner_id: str
    admin_ids: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "Operators":
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
            owner = data.get("owner_id")
            raw_admins = data.get("admin_ids", [])
            if not isinstance(raw_admins, list):
                raise ValueError("Invalid operator configuration.")
            admins = cast(list[object], raw_admins)
            if (
                set(data) not in ({"owner_id"}, {"owner_id", "admin_ids"})
                or not discord_id(owner)
                or not all(discord_id(item) for item in admins)
                or len(set(cast(list[str], admins))) != len(admins)
                or owner in admins
            ):
                raise ValueError("Invalid operator configuration.")
            return cls(cast(str, owner), tuple(cast(list[str], admins)))
        except (OSError, ValueError) as exc:
            raise AuthError("access_unavailable", 503) from exc


class Access:
    def __init__(
        self,
        database: str,
        operators: Operators,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.operators = operators
        self.accounts = Accounts(database)
        self._clock = clock

    @classmethod
    def from_file(cls, database: str, path: Path) -> "Access":
        return cls(database, Operators.load(path))

    async def close(self) -> None:
        await asyncio.to_thread(self.accounts.close)

    async def role(self, identifier: str) -> AccessRole | None:
        if identifier == self.operators.owner_id:
            return AccessRole.OWNER
        if identifier in self.operators.admin_ids:
            return AccessRole.ADMIN
        role = await asyncio.to_thread(self.accounts.access_role, identifier)
        return role if role is AccessRole.USER else None

    async def require(self, identifier: str) -> AccessRole:
        role = await self.role(identifier)
        if role is None:
            raise AuthError("access_denied", 403)
        return role

    async def require_admin(self, identifier: str) -> AccessRole:
        role = await self.require(identifier)
        if not role.privileged:
            raise AuthError("access_denied", 403)
        return role

    async def grants(self) -> tuple[AccessGrant, ...]:
        grants = await asyncio.to_thread(self.accounts.access_grants)
        operators = {self.operators.owner_id, *self.operators.admin_ids}
        return tuple(grant for grant in grants if grant.discord_id not in operators)

    async def history(self, limit: int = 100) -> tuple[AccessEvent, ...]:
        return await asyncio.to_thread(self.accounts.access_history, limit)

    async def grant(self, actor_id: str, discord_identifier: str) -> bool:
        await self.require_admin(actor_id)
        self._validate_subject(discord_identifier)
        changed = await asyncio.to_thread(
            self.accounts.grant_access, discord_identifier, actor_id, self._clock()
        )
        _LOGGER.info(
            "access.grant actor=%s subject=%s changed=%s",
            actor_id,
            discord_identifier,
            changed,
        )
        return changed

    async def revoke(self, actor_id: str, discord_identifier: str) -> bool:
        actor_role = await self.require_admin(actor_id)
        self._validate_subject(discord_identifier)
        try:
            changed = await asyncio.to_thread(
                self.accounts.revoke_access,
                discord_identifier,
                actor_id,
                self._clock(),
                owner=actor_role is AccessRole.OWNER,
            )
        except PermissionError as exc:
            raise AuthError("grant_not_owned", 403) from exc
        _LOGGER.info(
            "access.revoke actor=%s subject=%s changed=%s",
            actor_id,
            discord_identifier,
            changed,
        )
        return changed

    def _validate_subject(self, identifier: str) -> None:
        if not discord_id(identifier):
            raise AuthError("invalid_discord_id", 422)
        if (
            identifier == self.operators.owner_id
            or identifier in self.operators.admin_ids
        ):
            raise AuthError("operator_access_managed_in_config", 409)
