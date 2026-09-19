# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Resolve finite public YouTube videos to temporary audio stream URLs."""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

from .audio import ResolvedTrack, TrackError
from .processes import (
    ProcessOutputLimitError,
    ProcessResult,
    ProcessTimeoutError,
    run_process,
)

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}
_TRANSIENT_ERRORS = (
    "http error 429",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "connection reset",
    "connection timed out",
    "network is unreachable",
    "remote end closed connection",
    "temporary failure",
    "timed out",
    "unable to download webpage",
)
_PERMANENT_ERRORS = (
    "age-restricted",
    "confirm you're not a bot",
    "members-only",
    "private video",
    "sign in",
    "this video is unavailable",
    "video unavailable",
)


def _video_id(source_url: str) -> str | None:
    if not source_url or len(source_url) > 2048:
        return None
    try:
        parsed = urlsplit(source_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.username is not None:
        return None
    if parsed.password is not None or port not in {None, 80, 443}:
        return None

    if host == "youtu.be":
        parts = parsed.path.strip("/").split("/")
        candidate = parts[0] if len(parts) == 1 else ""
    elif host in _YOUTUBE_HOSTS:
        parts = parsed.path.strip("/").split("/")
        if parts == ["watch"]:
            values = parse_qs(parsed.query).get("v", [])
            candidate = values[0] if len(values) == 1 else ""
        elif len(parts) == 2 and parts[0] in {"shorts", "embed"}:
            candidate = parts[1]
        else:
            candidate = ""
    else:
        candidate = ""
    return candidate if _VIDEO_ID.fullmatch(candidate) else None


def _retryable_failure(stderr: bytes) -> bool:
    message = stderr.decode("utf-8", errors="replace").lower()
    if any(fragment in message for fragment in _PERMANENT_ERRORS):
        return False
    return any(fragment in message for fragment in _TRANSIENT_ERRORS)


def _parse_headers(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        return ()
    headers_value = cast(Mapping[object, object], value)
    headers: list[tuple[str, str]] = []
    for name, header_value in headers_value.items():
        if not isinstance(name, str) or not isinstance(header_value, str):
            raise TrackError("YouTube returned invalid stream metadata.")
        if not name or "\r" in name or "\n" in name:
            raise TrackError("YouTube returned invalid stream metadata.")
        if "\r" in header_value or "\n" in header_value:
            raise TrackError("YouTube returned invalid stream metadata.")
        headers.append((name, header_value))
    return tuple(headers)


def _resolved_track(result: ProcessResult) -> ResolvedTrack:
    if result.returncode == 2:
        raise RuntimeError("The YouTube resolver rejected its configuration.")
    if result.returncode != 0:
        raise TrackError(
            "YouTube could not resolve this video.",
            retryable=_retryable_failure(result.stderr),
        )
    try:
        decoded = cast(object, json.loads(result.stdout))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TrackError("YouTube returned invalid stream metadata.") from None
    if not isinstance(decoded, dict):
        raise TrackError("Only single YouTube videos are supported.")
    value = cast(dict[str, object], decoded)
    if value.get("_type") == "playlist":
        raise TrackError("Only single YouTube videos are supported.")

    duration = value.get("duration")
    live_status = value.get("live_status")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
        or value.get("is_live") is True
        or live_status in {"is_live", "is_upcoming", "post_live", "was_live"}
    ):
        raise TrackError("YouTube livestreams are not supported.")

    stream_url = value.get("url")
    if not isinstance(stream_url, str):
        raise TrackError("YouTube returned no playable audio stream.")
    try:
        parsed_stream = urlsplit(stream_url)
    except ValueError:
        raise TrackError("YouTube returned no playable audio stream.") from None
    if (
        parsed_stream.scheme not in {"http", "https"}
        or not parsed_stream.hostname
        or parsed_stream.username is not None
        or parsed_stream.password is not None
    ):
        raise TrackError("YouTube returned no playable audio stream.")
    title = value.get("title")
    uploader = value.get("uploader")
    return ResolvedTrack(
        stream_url,
        _parse_headers(value.get("http_headers")),
        value.get("acodec") == "opus",
        title=title if isinstance(title, str) else None,
        uploader=uploader if isinstance(uploader, str) else None,
    )


class YouTubeResolver:
    def __init__(self, node_path: Path, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._node_path = node_path
        self._timeout = timeout

    async def resolve(self, source_url: str) -> ResolvedTrack:
        if _video_id(source_url) is None:
            raise TrackError("Only single YouTube video URLs are supported.")

        args = (
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-plugin-dirs",
            "--no-playlist",
            "--no-js-runtimes",
            "--js-runtimes",
            f"node:{self._node_path}",
            "--no-remote-components",
            "--extractor-retries",
            "0",
            "--retries",
            "0",
            "--fragment-retries",
            "0",
            "--color",
            "never",
            "--format",
            "bestaudio/best",
            "--dump-single-json",
            "--simulate",
            "--",
            source_url,
        )
        try:
            result = await run_process(args, timeout=self._timeout)
        except ProcessTimeoutError as error:
            raise TrackError(
                "YouTube took too long to resolve this video.", retryable=True
            ) from error
        except ProcessOutputLimitError as error:
            raise TrackError("YouTube returned too much stream metadata.") from error
        except OSError as error:
            raise RuntimeError("The YouTube resolver could not be started.") from error
        return _resolved_track(result)
