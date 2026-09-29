# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Opaque positions shared by stable API pagination contracts."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .errors import ApiError, ApiErrorCode


class NumberedPageView[ItemT](BaseModel):
    """Stable numbered page pinned to an optional read snapshot."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[ItemT, ...]
    page: int
    page_size: int
    total: int
    page_count: int
    snapshot: str | None


class CursorPageView[ItemT](BaseModel):
    """Descending cursor page whose next position is opaque to clients."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[ItemT, ...]
    page_size: int
    next_cursor: str | None


class OffsetPageView[ItemT](BaseModel):
    """Offset page used by bounded immutable discovery snapshots."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[ItemT, ...]
    offset: int
    page_size: int
    total: int
    next_offset: int | None


class LiveDeltaView[ItemT](BaseModel):
    """Incremental live feed with the latest observed cursor."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[ItemT, ...]
    cursor: int


@dataclass(frozen=True, slots=True)
class TimestampPosition:
    occurred_at: datetime
    identifier: UUID


@dataclass(frozen=True, slots=True)
class RevisionPosition:
    resource_id: UUID
    revision: int


def encode_position(position: TimestampPosition | None) -> str | None:
    if position is None:
        return None
    payload = (
        f"{position.occurred_at.astimezone(UTC).isoformat()}|{position.identifier}"
    )
    return urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def encode_revision(position: RevisionPosition) -> str:
    payload = f"{position.resource_id}|{position.revision}"
    return urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def encode_timestamp(value: datetime) -> str:
    """Encode one UTC time boundary as an opaque page snapshot."""
    payload = value.astimezone(UTC).isoformat()
    return urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_position(value: str | None) -> TimestampPosition | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload = urlsafe_b64decode(value + padding).decode()
        occurred_at_value, identifier_value = payload.split("|", 1)
        occurred_at = datetime.fromisoformat(occurred_at_value)
        if occurred_at.tzinfo is None:
            raise ValueError("Pagination timestamp must have a timezone.")
        return TimestampPosition(
            occurred_at.astimezone(UTC),
            UUID(identifier_value),
        )
    except (Base64Error, UnicodeDecodeError, ValueError) as error:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error


def decode_revision(value: str | None) -> RevisionPosition | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload = urlsafe_b64decode(value + padding).decode()
        resource_id, revision = payload.split("|", 1)
        parsed_revision = int(revision)
        if parsed_revision < 0:
            raise ValueError("Pagination revision must be non-negative.")
        return RevisionPosition(UUID(resource_id), parsed_revision)
    except (Base64Error, UnicodeDecodeError, ValueError) as error:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error


def decode_timestamp(value: str | None) -> datetime | None:
    """Decode an opaque UTC time boundary used by numbered pages."""
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        timestamp = datetime.fromisoformat(urlsafe_b64decode(value + padding).decode())
        if timestamp.tzinfo is None:
            raise ValueError("Pagination timestamp must have a timezone.")
        return timestamp.astimezone(UTC)
    except (Base64Error, UnicodeDecodeError, ValueError) as error:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
