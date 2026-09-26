# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""YouTube Music discovery and YouTube link details for the new catalog."""

import asyncio
import json
import logging
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast
from urllib.parse import parse_qs, urlsplit

from nahoermaar.catalog.domain import (
    MediaKind,
    MediaReference,
    ObservationQuality,
    ProviderName,
    SourceAvailability,
)
from nahoermaar.catalog.providers import (
    ProviderArtist,
    ProviderAudio,
    ProviderError,
    ProviderPage,
    ProviderPlaylist,
    ProviderTrack,
)

from .processes import (
    ProcessCleanupError,
    ProcessOutputLimitError,
    ProcessResult,
    ProcessTimeoutError,
    run_process,
)

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{10,150}")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_LOGGER = logging.getLogger(__name__)
_TRANSIENT_ERRORS = (
    "http error 429",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "connection reset",
    "connection timed out",
    "network is unreachable",
    "temporary failure",
    "timed out",
)
_PERMANENT_ERRORS = (
    "age-restricted",
    "confirm you're not a bot",
    "members-only",
    "private video",
    "sign in",
    "video unavailable",
)


class ProcessRunner(Protocol):
    async def __call__(
        self, args: tuple[str, ...], *, timeout: float
    ) -> ProcessResult: ...


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _duration(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    text = _text(value)
    if text is None:
        return None
    parts = text.split(":")
    if not 1 <= len(parts) <= 3 or not all(
        part.isascii() and part.isdecimal() for part in parts
    ):
        return None
    return float(
        sum(int(part) * 60**power for power, part in enumerate(reversed(parts)))
    )


def _thumbnail(value: object) -> str | None:
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, list):
        return None
    candidates = [
        _text(cast(dict[str, object], item).get("url"))
        for item in cast(list[object], value)
        if isinstance(item, dict)
    ]
    return next((candidate for candidate in reversed(candidates) if candidate), None)


def _release_date(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _payload(result: ProcessResult, *, partial: bool = False) -> dict[str, object]:
    if result.returncode != 0 and (not partial or not result.stdout.strip()):
        message = result.stderr.decode("utf-8", errors="replace").lower()
        retryable = any(part in message for part in _TRANSIENT_ERRORS) and not any(
            part in message for part in _PERMANENT_ERRORS
        )
        raise ProviderError("YouTube could not load this content.", retryable=retryable)
    try:
        decoded: object = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        raise ProviderError("YouTube returned invalid metadata.") from None
    if not isinstance(decoded, dict):
        raise ProviderError("YouTube returned invalid metadata.")
    return cast(dict[str, object], decoded)


def _music_track(raw: object) -> ProviderTrack | None:
    value = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    external_id = _text(value.get("videoId"))
    title = _text(value.get("title"))
    if external_id is None or _VIDEO_ID.fullmatch(external_id) is None or title is None:
        return None
    raw_artists = value.get("artists")
    artists: list[ProviderArtist] = []
    names: list[str] = []
    if isinstance(raw_artists, list):
        for raw_artist in cast(list[object], raw_artists):
            artist = (
                cast(dict[str, object], raw_artist)
                if isinstance(raw_artist, dict)
                else {}
            )
            name = _text(artist.get("name"))
            identity = _text(artist.get("id"))
            if name is not None:
                names.append(name)
            if name is not None and identity is not None:
                candidate = ProviderArtist(ProviderName.YOUTUBE_MUSIC, identity, name)
                if candidate not in artists:
                    artists.append(candidate)
    album = (
        cast(dict[str, object], value.get("album"))
        if isinstance(value.get("album"), dict)
        else {}
    )
    return ProviderTrack(
        ProviderName.YOUTUBE,
        external_id,
        f"https://music.youtube.com/watch?v={external_id}",
        title,
        ", ".join(names) or None,
        tuple(artists),
        _duration(value.get("duration_seconds") or value.get("duration")),
        _thumbnail(value.get("thumbnails") or value.get("thumbnail")),
        album_title=_text(album.get("name")),
    )


def _video_track(raw: object, *, quality: ObservationQuality) -> ProviderTrack | None:
    value = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    external_id = _text(value.get("id"))
    title = _text(value.get("track")) or _text(value.get("title"))
    if external_id is None or _VIDEO_ID.fullmatch(external_id) is None or title is None:
        return None
    unavailable = value.get("availability") in {
        "private",
        "premium_only",
        "subscriber_only",
        "needs_auth",
    } or title in {"[Private video]", "[Deleted video]"}
    if value.get("is_live") is True or value.get("live_status") in {
        "is_live",
        "is_upcoming",
        "post_live",
        "was_live",
    }:
        return None
    artist = _text(value.get("artist"))
    artist_identity = _text(value.get("channel_id")) or _text(value.get("uploader_id"))
    artists = (
        (ProviderArtist(ProviderName.YOUTUBE, artist_identity, artist),)
        if artist is not None and artist_identity is not None
        else ()
    )
    return ProviderTrack(
        ProviderName.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
        title,
        artist,
        artists,
        _duration(value.get("duration")),
        _thumbnail(value.get("thumbnail") or value.get("thumbnails")),
        _text(value.get("uploader")) or _text(value.get("channel")),
        _text(value.get("channel_url")) or _text(value.get("uploader_url")),
        _text(value.get("album")),
        _release_date(value.get("release_date") or value.get("upload_date")),
        _text(value.get("isrc")),
        quality,
        SourceAvailability.UNAVAILABLE if unavailable else SourceAvailability.AVAILABLE,
    )


def _ytdlp(node_path: Path, source: str, options: tuple[str, ...]) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-cache-dir",
        "--no-plugin-dirs",
        "--no-remote-components",
        "--no-js-runtimes",
        "--js-runtimes",
        f"node:{node_path}",
        "--extractor-retries",
        "0",
        "--retries",
        "0",
        "--color",
        "never",
        "--simulate",
        *options,
        "--",
        source,
    )


class YouTubeProvider:
    key = "youtube"

    def __init__(
        self,
        node_path: Path,
        *,
        timeout: float = 30.0,
        runner: ProcessRunner = run_process,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("YouTube timeout must be positive and finite.")
        self._node_path = node_path
        self._timeout = timeout
        self._runner = runner
        self._requests: set[asyncio.Task[ProcessResult]] = set()
        self._closed = False

    @staticmethod
    def identify(
        source_url: str, *, kind: MediaKind | None = None
    ) -> MediaReference | None:
        if len(source_url) > 2048 or any(
            ord(character) < 32 for character in source_url
        ):
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
        if parsed.hostname == "youtu.be" and len(parts) == 1:
            video = parts[0]
        elif parts == ["watch"]:
            values = query.get("v", [])
            video = values[0] if len(values) == 1 else ""
        elif len(parts) == 2 and parts[0] in {"embed", "shorts"}:
            video = parts[1]
        video = video if _VIDEO_ID.fullmatch(video) else ""
        values = query.get("list", [])
        playlist = values[0] if len(values) == 1 else ""
        if kind in {None, MediaKind.TRACK} and video:
            return MediaReference(
                ProviderName.YOUTUBE,
                video,
                MediaKind.TRACK,
                f"https://www.youtube.com/watch?v={video}",
            )
        if (
            kind in {None, MediaKind.PLAYLIST}
            and _PLAYLIST_ID.fullmatch(playlist)
            and (video or parts == ["playlist"])
        ):
            return MediaReference(
                ProviderName.YOUTUBE,
                playlist,
                MediaKind.PLAYLIST,
                f"https://www.youtube.com/playlist?list={playlist}",
            )
        return None

    async def search(self, query: str, *, limit: int) -> ProviderPage:
        started_at = perf_counter()
        _LOGGER.debug("youtube.search_started limit=%d", limit)
        result = await self._execute(
            _ytdlp(
                self._node_path,
                f"ytsearch{limit}:{query}",
                ("--flat-playlist", "--dump-single-json"),
            ),
            operation="search",
        )
        entries = _payload(result).get("entries")
        if not isinstance(entries, list):
            raise ProviderError("YouTube returned invalid search results.")
        page = ProviderPage(
            tuple(
                track
                for entry in cast(list[object], entries)
                if (
                    track := _video_track(
                        entry,
                        quality=ObservationQuality.DISCOVERY,
                    )
                )
                is not None
            )[:limit]
        )
        _LOGGER.info(
            "youtube.search_completed entries=%d duration_ms=%.1f",
            len(page.entries),
            (perf_counter() - started_at) * 1000,
        )
        return page

    async def playlist(
        self, reference: MediaReference, *, limit: int
    ) -> ProviderPlaylist:
        if (
            reference.provider is not ProviderName.YOUTUBE
            or reference.kind is not MediaKind.PLAYLIST
        ):
            raise ProviderError("YouTube cannot load this playlist identity.")
        started_at = perf_counter()
        _LOGGER.debug(
            "youtube.playlist_started external_id=%s limit=%d",
            reference.external_id,
            limit,
        )
        result = await self._execute(
            _ytdlp(
                self._node_path,
                reference.source_url,
                (
                    "--yes-playlist",
                    "--flat-playlist",
                    "--ignore-errors",
                    "--dump-single-json",
                    "--playlist-items",
                    f"1:{limit + 1}",
                ),
            ),
            operation="playlist",
        )
        payload = _payload(result, partial=True)
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise ProviderError("YouTube returned invalid playlist results.")
        converted = tuple(
            track
            for entry in cast(list[object], raw_entries)
            if (track := _video_track(entry, quality=ObservationQuality.DISCOVERY))
            is not None
        )
        page = ProviderPage(
            converted[:limit], str(limit) if len(converted) > limit else None
        )
        playlist = ProviderPlaylist(reference, _text(payload.get("title")), page)
        _LOGGER.info(
            "youtube.playlist_completed external_id=%s entries=%d has_more=%s "
            "duration_ms=%.1f",
            reference.external_id,
            len(page.entries),
            page.continuation is not None,
            (perf_counter() - started_at) * 1000,
        )
        return playlist

    async def radio(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        if reference.provider is not ProviderName.YOUTUBE:
            raise ProviderError("YouTube cannot load this radio identity.")
        if continuation is not None:
            raise ProviderError("YouTube Music radio has no continuation.")
        started_at = perf_counter()
        _LOGGER.debug(
            "youtube.radio_started kind=%s external_id=%s limit=%d",
            reference.kind.value,
            reference.external_id,
            limit,
        )
        result = await self._execute(
            (
                sys.executable,
                "-m",
                "nahoermaar.integrations.youtube",
                "radio",
                reference.kind.value,
                reference.external_id,
                str(limit),
            ),
            operation="radio",
        )
        entries = _payload(result).get("entries")
        if not isinstance(entries, list):
            raise ProviderError("YouTube Music returned invalid radio results.")
        page = ProviderPage(
            tuple(
                track
                for entry in cast(list[object], entries)
                if (track := _music_track(entry)) is not None
            )[:limit]
        )
        _LOGGER.info(
            "youtube.radio_completed kind=%s external_id=%s entries=%d "
            "duration_ms=%.1f",
            reference.kind.value,
            reference.external_id,
            len(page.entries),
            (perf_counter() - started_at) * 1000,
        )
        return page

    async def track(self, reference: MediaReference) -> ProviderTrack:
        if (
            reference.provider is not ProviderName.YOUTUBE
            or reference.kind is not MediaKind.TRACK
        ):
            raise ProviderError("YouTube cannot load this track identity.")
        started_at = perf_counter()
        _LOGGER.debug("youtube.track_started external_id=%s", reference.external_id)
        result = await self._execute(
            _ytdlp(
                self._node_path,
                reference.source_url,
                ("--no-playlist", "--dump-single-json"),
            ),
            operation="track",
        )
        track = _video_track(_payload(result), quality=ObservationQuality.DETAIL)
        if (
            track is None
            or track.external_id != reference.external_id
            or track.availability is SourceAvailability.UNAVAILABLE
        ):
            raise ProviderError("YouTube returned no available track.")
        _LOGGER.info(
            "youtube.track_completed external_id=%s duration_seconds=%s "
            "duration_ms=%.1f",
            reference.external_id,
            track.duration_seconds,
            (perf_counter() - started_at) * 1000,
        )
        return track

    async def resolve_audio(self, reference: MediaReference) -> ProviderAudio:
        if (
            reference.provider is not ProviderName.YOUTUBE
            or reference.kind is not MediaKind.TRACK
        ):
            raise ProviderError("YouTube cannot resolve this audio identity.")
        started_at = perf_counter()
        result = await self._execute(
            _ytdlp(
                self._node_path,
                reference.source_url,
                (
                    "--no-playlist",
                    "--fragment-retries",
                    "0",
                    "--format",
                    "bestaudio/best",
                    "--dump-single-json",
                ),
            ),
            operation="audio",
        )
        value = _payload(result)
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
        _LOGGER.info(
            "youtube.audio_resolved external_id=%s opus=%s duration_ms=%.1f",
            reference.external_id,
            value.get("acodec") == "opus",
            (perf_counter() - started_at) * 1000,
        )
        return ProviderAudio(
            stream_url,
            tuple(headers),
            value.get("acodec") == "opus",
        )

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(task for task in self._requests if not task.done())
        _LOGGER.info("youtube.closing active_requests=%d", len(tasks))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _LOGGER.info("youtube.closed")

    async def _execute(
        self, arguments: tuple[str, ...], *, operation: str
    ) -> ProcessResult:
        if self._closed:
            _LOGGER.warning(
                "youtube.request_rejected operation=%s reason=provider_closed",
                operation,
            )
            raise ProviderError("YouTube provider is closed.")
        task = asyncio.create_task(self._runner(arguments, timeout=self._timeout))
        self._requests.add(task)
        try:
            return await task
        except ProcessTimeoutError:
            _LOGGER.warning(
                "youtube.request_failed operation=%s reason=timeout", operation
            )
            raise ProviderError(
                "YouTube took too long to respond.", retryable=True
            ) from None
        except ProcessOutputLimitError:
            _LOGGER.warning(
                "youtube.request_failed operation=%s reason=output_limit", operation
            )
            raise ProviderError("YouTube returned too much metadata.") from None
        except ProcessCleanupError:
            _LOGGER.warning(
                "youtube.request_failed operation=%s reason=cleanup", operation
            )
            raise ProviderError("YouTube cleanup failed.") from None
        except OSError:
            _LOGGER.warning(
                "youtube.request_failed operation=%s reason=process_start", operation
            )
            raise ProviderError("YouTube resolver could not be started.") from None
        finally:
            self._requests.discard(task)


class YouTubeMusicProvider(YouTubeProvider):
    """Music search with shared YouTube links, radio and audio resolution."""

    key = "youtube_music"

    async def search(self, query: str, *, limit: int) -> ProviderPage:
        started_at = perf_counter()
        _LOGGER.debug("youtube_music.search_started limit=%d", limit)
        result = await self._execute(
            (
                sys.executable,
                "-m",
                "nahoermaar.integrations.youtube",
                "search",
                query,
                str(limit),
            ),
            operation="search",
        )
        entries = _payload(result).get("entries")
        if not isinstance(entries, list):
            raise ProviderError("YouTube Music returned invalid search results.")
        page = ProviderPage(
            tuple(
                track
                for entry in cast(list[object], entries)
                if (track := _music_track(entry)) is not None
            )[:limit]
        )
        _LOGGER.info(
            "youtube_music.search_completed entries=%d duration_ms=%.1f",
            len(page.entries),
            (perf_counter() - started_at) * 1000,
        )
        return page


def _worker_main() -> None:
    from ytmusicapi import YTMusic

    entries: object
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "search":
            entries = YTMusic().search(
                sys.argv[2],
                filter="songs",
                limit=int(sys.argv[3]),
            )
        elif len(sys.argv) == 5 and sys.argv[1] == "radio":
            kind, identity, limit = sys.argv[2:5]
            result = (
                YTMusic().get_watch_playlist(
                    videoId=identity,
                    radio=True,
                    limit=int(limit),
                )
                if kind == MediaKind.TRACK.value
                else YTMusic().get_watch_playlist(
                    playlistId=f"RDAMPL{identity}",
                    limit=int(limit),
                )
            )
            entries = result.get("tracks")
            if not isinstance(entries, list):
                raise ValueError("Radio response has no tracks.")
        else:
            raise ValueError("Invalid worker request.")
        sys.stdout.buffer.write(
            json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8")
        )
    except Exception:
        raise SystemExit("YouTube Music request failed.") from None


if __name__ == "__main__":
    _worker_main()
