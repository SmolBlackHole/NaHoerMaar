# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""YouTube/Music discovery and audio, independent of the legacy playback core."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import parse_qs, urlsplit

from ..integrations.processes import (
    ProcessCleanupError,
    ProcessOutputLimitError,
    ProcessResult,
    ProcessTimeoutError,
    run_process,
)
from ..integrations.ytdlp import ytdlp_arguments
from .audio import PlayableSource
from .domain.catalog import (
    ArtistCredit,
    MediaKind,
    MediaReference,
    PlaylistPage,
    TrackFinding,
    TrackPage,
    UnavailableFinding,
)
from .domain.metadata import TrackMetadata
from .domain.tracks import ArtistIdentity, MediaIdentity
from .observability import logged_operation
from .providers import ProviderError, UnsupportedCapability

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{10,150}")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_MAX_RESULTS = 100
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


class ProcessRunner(Protocol):
    async def __call__(
        self, args: tuple[str, ...], *, timeout: float
    ) -> ProcessResult: ...


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _payload(result: ProcessResult, *, partial: bool = False) -> dict[str, object]:
    if result.returncode == 2:
        raise ProviderError("The YouTube resolver rejected its configuration.")
    if result.returncode != 0 and (not partial or not result.stdout.strip()):
        message = result.stderr.decode("utf-8", errors="replace").lower()
        retryable = any(part in message for part in _TRANSIENT_ERRORS) and not any(
            part in message for part in _PERMANENT_ERRORS
        )
        raise ProviderError(
            "YouTube could not resolve this track.", retryable=retryable
        )
    try:
        decoded: object = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        raise ProviderError("YouTube returned invalid track metadata.") from None
    if not isinstance(decoded, dict):
        raise ProviderError("YouTube returned invalid track metadata.")
    return cast(dict[str, object], decoded)


def _finding(
    raw: object, *, host: str = "www.youtube.com"
) -> TrackFinding | UnavailableFinding:
    value = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    duration = value.get("duration")
    try:
        seconds = (
            float(duration)
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else None
        )
    except OverflowError:
        seconds = None
    if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
        seconds = None
    thumbnail = _text(value.get("thumbnail"))
    thumbnails = value.get("thumbnails")
    if thumbnail is None and isinstance(thumbnails, list):
        for candidate in cast(list[object], thumbnails):
            if isinstance(candidate, dict):
                thumbnail = (
                    _text(cast(dict[str, object], candidate).get("url")) or thumbnail
                )
    metadata = TrackMetadata(
        title=_text(value.get("track")) or _text(value.get("title")),
        artist=_text(value.get("artist")),
        uploader=_text(value.get("uploader")) or _text(value.get("channel")),
        uploader_url=_text(value.get("channel_url"))
        or _text(value.get("uploader_url")),
        duration_seconds=seconds,
        thumbnail_url=thumbnail,
    )
    identifier = _text(value.get("id"))
    reference = (
        MediaReference(
            MediaIdentity("youtube", identifier),
            MediaKind.TRACK,
            f"https://{host}/watch?v={identifier}",
        )
        if identifier and _VIDEO_ID.fullmatch(identifier)
        else None
    )
    if (
        reference is None
        or value.get("availability")
        in ("private", "premium_only", "subscriber_only", "needs_auth")
        or metadata.title in ("[Private video]", "[Deleted video]")
    ):
        return UnavailableFinding(
            "This YouTube track is unavailable.", metadata, reference
        )
    if value.get("is_live") is True or value.get("live_status") in (
        "is_live",
        "is_upcoming",
        "post_live",
        "was_live",
    ):
        return UnavailableFinding(
            "YouTube livestreams are not supported.", metadata, reference
        )
    return TrackFinding(reference, metadata)


def _music_finding(raw: object) -> TrackFinding | UnavailableFinding:
    value = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    raw_artists = value.get("artists")
    complete_credits = isinstance(raw_artists, list)
    names: list[str] = []
    credits: dict[ArtistIdentity, ArtistCredit] = {}
    if isinstance(raw_artists, list):
        for raw_artist in cast(list[object], raw_artists):
            artist = (
                cast(dict[str, object], raw_artist)
                if isinstance(raw_artist, dict)
                else {}
            )
            name, identifier = _text(artist.get("name")), _text(artist.get("id"))
            identity = ArtistIdentity("youtube", identifier) if identifier else None
            if identity in credits:
                continue
            if name:
                names.append(name)
            if name and identity:
                credits[identity] = ArtistCredit(identity, name)
            else:
                complete_credits = False
    duration = value.get("duration_seconds")
    if duration is None:
        formatted = _text(value.get("duration")) or _text(value.get("length"))
        if formatted:
            parts = formatted.split(":")
            if len(parts) <= 3 and all(
                len(part) <= 3 and part.isascii() and part.isdecimal() for part in parts
            ):
                duration = sum(
                    int(part) * 60**power for power, part in enumerate(reversed(parts))
                )
    finding = _finding(
        {
            "id": value.get("videoId"),
            "title": value.get("title"),
            "artist": ", ".join(names) or None,
            "duration": duration,
            "thumbnails": value.get("thumbnails", value.get("thumbnail")),
            "availability": "private" if value.get("isAvailable") is False else None,
            "is_live": value.get("isLive"),
        },
        host="music.youtube.com",
    )
    if isinstance(finding, TrackFinding) and complete_credits:
        finding = replace(finding, artists=tuple(credits.values()))
    return finding


def _limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= _MAX_RESULTS:
        raise ValueError(f"Result limit must be between 1 and {_MAX_RESULTS}.")


def _search_query(query: str, limit: int, continuation: str | None) -> str:
    _limit(limit)
    query = " ".join(query.split())
    if not 1 <= len(query) <= 200:
        raise ValueError("Search must contain 1 to 200 characters.")
    if continuation is not None:
        raise UnsupportedCapability(
            "Search returns a bounded result pool, not provider pages."
        )
    return query


class YouTubeProvider:
    """Video discovery, single-track details and audio in the YouTube namespace.

    The injected runner owns subprocess cleanup; this adapter owns its request
    tasks. Neither construction nor link identification starts external work.
    Search pools are intended for catalog snapshot paging; playlists use source offsets.
    Neither adapter-owned result caches nor automatic queue changes live here.
    """

    key = "youtube"

    def __init__(
        self,
        node_path: Path,
        *,
        timeout: float = 30.0,
        runner: ProcessRunner = run_process,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self._node_path = node_path
        self._timeout = timeout
        self._runner = runner
        self._requests: set[asyncio.Task[ProcessResult]] = set()
        self._closed = False

    @staticmethod
    def identify(
        source_url: str, *, kind: MediaKind | None = None
    ) -> MediaReference | None:
        if len(source_url) > 2048 or any(ord(char) < 32 for char in source_url):
            return None
        try:
            parsed = urlsplit(source_url.strip())
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in _HOSTS | {"youtu.be"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in {None, 80, 443}
            ):
                return None
        except ValueError:
            return None
        parts = parsed.path.strip("/").split("/")
        query = parse_qs(parsed.query, keep_blank_values=True)
        video = ""
        if parsed.hostname == "youtu.be":
            if len(parts) == 1:
                video = parts[0]
        elif parts == ["watch"]:
            values = query.get("v", [])
            video = values[0] if len(values) == 1 else ""
        elif len(parts) == 2 and parts[0] in {"embed", "shorts"}:
            video = parts[1]
        video = video if _VIDEO_ID.fullmatch(video) else ""
        host = (
            "music.youtube.com"
            if parsed.hostname == "music.youtube.com"
            else "www.youtube.com"
        )
        if video and kind in {None, MediaKind.TRACK}:
            return MediaReference(
                MediaIdentity("youtube", video),
                MediaKind.TRACK,
                f"https://{host}/watch?v={video}",
            )
        values = query.get("list", [])
        playlist = values[0] if len(values) == 1 else ""
        if (
            kind in {None, MediaKind.PLAYLIST}
            and (video or (parts == ["playlist"] and parsed.hostname in _HOSTS))
            and _PLAYLIST_ID.fullmatch(playlist)
        ):
            return MediaReference(
                MediaIdentity("youtube", playlist),
                MediaKind.PLAYLIST,
                f"https://{host}/playlist?list={playlist}",
            )
        return None

    @logged_operation("engine.youtube.request")
    async def _execute(self, args: tuple[str, ...]) -> ProcessResult:
        if self._closed:
            raise ProviderError("The YouTube provider is closed.")
        request = asyncio.create_task(self._runner(args, timeout=self._timeout))
        self._requests.add(request)
        try:
            return await request
        except ProcessTimeoutError:
            raise ProviderError(
                "YouTube took too long to respond.", retryable=True
            ) from None
        except ProcessOutputLimitError:
            raise ProviderError("YouTube returned too much metadata.") from None
        except ProcessCleanupError:
            raise ProviderError(
                "The YouTube request could not be cleaned up."
            ) from None
        except OSError:
            raise ProviderError("The YouTube resolver could not be started.") from None
        finally:
            self._requests.discard(request)

    async def _extract(
        self, identity: MediaIdentity
    ) -> tuple[TrackFinding, dict[str, object]]:
        if identity.namespace != "youtube" or not _VIDEO_ID.fullmatch(
            identity.external_id
        ):
            raise ProviderError("Only YouTube track identities are supported.")
        args = ytdlp_arguments(
            self._node_path,
            f"https://www.youtube.com/watch?v={identity.external_id}",
            (
                "--no-playlist",
                "--fragment-retries",
                "0",
                "--format",
                "bestaudio/best",
                "--dump-single-json",
            ),
        )
        value = _payload(await self._execute(args))
        if value.get("_type") not in (None, "video"):
            raise ProviderError("Only single YouTube tracks are supported.")
        if value.get("id") != identity.external_id:
            raise ProviderError("YouTube returned a different track identity.")
        finding = _finding(value)
        if isinstance(finding, UnavailableFinding):
            raise ProviderError(finding.reason)
        return finding, value

    async def search(
        self, query: str, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        query = _search_query(query, limit, continuation)
        args = ytdlp_arguments(
            self._node_path,
            f"ytsearch{limit}:{query}",
            ("--flat-playlist", "--dump-single-json"),
        )
        entries = _payload(await self._execute(args)).get("entries")
        if not isinstance(entries, list):
            raise ProviderError("YouTube returned invalid search results.")
        return TrackPage(
            tuple(_finding(entry) for entry in cast(list[object], entries)[:limit])
        )

    async def playlist(
        self, identity: MediaIdentity, *, limit: int, continuation: str | None = None
    ) -> PlaylistPage:
        _limit(limit)
        if identity.namespace != "youtube" or not _PLAYLIST_ID.fullmatch(
            identity.external_id
        ):
            raise ProviderError("Only YouTube playlist identities are supported.")
        offset = 0
        if continuation is not None:
            try:
                decoded: object = (
                    json.loads(continuation) if len(continuation) <= 512 else None
                )
            except ValueError:
                decoded = None
            cursor = cast(list[object], decoded) if isinstance(decoded, list) else []
            if (
                len(cursor) != 3
                or cursor[:2] != [self.key, identity.external_id]
                or type(cursor[2]) is not int
                or cursor[2] < 1
            ):
                raise ValueError(
                    "Invalid playlist continuation for this provider and playlist."
                )
            offset = cursor[2]
        reference = MediaReference(
            identity,
            MediaKind.PLAYLIST,
            f"https://www.youtube.com/playlist?list={identity.external_id}",
        )
        args = ytdlp_arguments(
            self._node_path,
            reference.source_url,
            (
                "--yes-playlist",
                "--flat-playlist",
                "--ignore-errors",
                "--dump-single-json",
                "--playlist-items",
                f"{offset + 1}:{offset + limit + 1}",
            ),
        )
        result = await self._execute(args)
        value = _payload(result, partial=True)
        if value.get("id") != identity.external_id or value.get("_type") != "playlist":
            raise ProviderError("YouTube returned a different playlist identity.")
        raw_entries = value.get("entries")
        if not isinstance(raw_entries, list):
            raise ProviderError("YouTube returned invalid playlist entries.")
        entries = cast(list[object], raw_entries)
        if result.returncode != 0 and not entries:
            raise ProviderError("The playlist could not be loaded.")
        positions = value.get("requested_entries")
        if positions is not None:
            if not isinstance(positions, list):
                raise ProviderError("YouTube returned invalid playlist positions.")
            indices = cast(list[object], positions)
            if len(indices) != len(entries):
                raise ProviderError("YouTube returned invalid playlist positions.")
            ordered: dict[int, object] = {}
            previous = offset
            for position, entry in zip(indices, entries, strict=True):
                if (
                    type(position) is not int
                    or not previous < position <= offset + limit + 1
                ):
                    raise ProviderError("YouTube returned invalid playlist positions.")
                ordered[position] = entry
                previous = position
            entries = [
                ordered.get(position) for position in range(offset + 1, previous + 1)
            ]
        page = TrackPage(
            tuple(_finding(entry) for entry in entries[:limit]),
            json.dumps(
                [self.key, identity.external_id, offset + limit], separators=(",", ":")
            )
            if len(entries) > limit and result.returncode == 0
            else None,
            "Some playlist entries could not be loaded."
            if result.returncode != 0
            else None,
        )
        return PlaylistPage(reference, _text(value.get("title")), page)

    async def track(self, identity: MediaIdentity) -> TrackFinding:
        finding, _ = await self._extract(identity)
        return finding

    async def resolve_audio(self, identity: MediaIdentity) -> PlayableSource:
        finding, value = await self._extract(identity)
        if finding.metadata.duration_seconds is None:
            raise ProviderError("YouTube returned no finite track duration.")
        stream_url = _text(value.get("url"))
        try:
            parsed = urlsplit(stream_url or "")
            valid_stream = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname is not None
                and parsed.username is None
                and parsed.password is None
                and parsed.port != 0
            )
        except ValueError:
            valid_stream = False
        if stream_url is None or not valid_stream:
            raise ProviderError("YouTube returned no playable audio stream.")
        headers: list[tuple[str, str]] = []
        raw_headers = value.get("http_headers")
        if raw_headers is not None:
            if not isinstance(raw_headers, dict):
                raise ProviderError("YouTube returned invalid stream headers.")
            for name, header in cast(dict[str, object], raw_headers).items():
                if (
                    not name.strip()
                    or "\r" in name
                    or "\n" in name
                    or not isinstance(header, str)
                    or "\r" in header
                    or "\n" in header
                ):
                    raise ProviderError("YouTube returned invalid stream headers.")
                headers.append((name, header))
        return PlayableSource(
            finding, stream_url, tuple(headers), value.get("acodec") == "opus"
        )

    async def close(self) -> None:
        self._closed = True
        requests = tuple(request for request in self._requests if not request.done())
        for request in requests:
            request.cancel()
        results = await asyncio.gather(*requests, return_exceptions=True)
        if any(isinstance(result, Exception) for result in results):
            raise ProviderError("The YouTube request could not be cleaned up.")


class YouTubeMusicProvider(YouTubeProvider):
    """Music search with shared YouTube link, playlist and playback capabilities."""

    key = "youtube_music"

    async def radio_next(
        self, seed: MediaReference, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        _limit(limit)
        pattern = _VIDEO_ID if seed.kind is MediaKind.TRACK else _PLAYLIST_ID
        if seed.identity.namespace != "youtube" or not pattern.fullmatch(
            seed.identity.external_id
        ):
            raise UnsupportedCapability("This radio seed is not supported.")
        if continuation is not None:
            raise UnsupportedCapability(
                "Music radio returns a bounded recommendation pool, not native pages."
            )
        result = await self._execute(
            (
                sys.executable,
                "-m",
                "nahormaar_backend.integrations.music_radio",
                seed.kind.value,
                seed.identity.external_id,
                str(limit),
            )
        )
        entries = _payload(result).get("tracks")
        if not isinstance(entries, list):
            raise ProviderError("YouTube Music returned invalid radio recommendations.")
        return TrackPage(
            tuple(
                _music_finding(entry) for entry in cast(list[object], entries)[:limit]
            )
        )

    async def search(
        self, query: str, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        query = _search_query(query, limit, continuation)
        result = await self._execute(
            (
                sys.executable,
                "-m",
                "nahormaar_backend.integrations.music_search",
                query,
                str(limit),
            )
        )
        entries = _payload(result).get("entries")
        if not isinstance(entries, list):
            raise ProviderError("YouTube Music returned invalid search results.")
        return TrackPage(
            tuple(
                _music_finding(entry) for entry in cast(list[object], entries)[:limit]
            )
        )
