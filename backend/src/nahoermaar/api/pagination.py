# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Opaque positions shared by stable API pagination contracts."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from .errors import ApiError, ApiErrorCode


@dataclass(frozen=True, slots=True)
class TimestampPosition:
    occurred_at: datetime
    identifier: UUID


def encode_position(position: TimestampPosition | None) -> str | None:
    if position is None:
        return None
    payload = (
        f"{position.occurred_at.astimezone(UTC).isoformat()}|{position.identifier}"
    )
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
