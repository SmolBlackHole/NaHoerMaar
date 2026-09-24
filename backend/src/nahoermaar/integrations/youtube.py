# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""YouTube Music discovery and YouTube link details for the new catalog."""

import asyncio
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
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
    return ProviderTrack(
        ProviderName.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
        title,
        artist,
        (),
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
    key = "youtube_music"

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
        result = await self._execute(
            (
                sys.executable,
                "-m",
                "nahoermaar.integrations.youtube",
                "search",
                query,
                str(limit),
            )
        )
        entries = _payload(result).get("entries")
        if not isinstance(entries, list):
            raise ProviderError("YouTube Music returned invalid search results.")
        return ProviderPage(
            tuple(
                track
                for entry in cast(list[object], entries)
                if (track := _music_track(entry)) is not None
            )[:limit]
        )

    async def playlist(
        self, reference: MediaReference, *, limit: int
    ) -> ProviderPlaylist:
        if (
            reference.provider is not ProviderName.YOUTUBE
            or reference.kind is not MediaKind.PLAYLIST
        ):
            raise ProviderError("YouTube cannot load this playlist identity.")
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
            )
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
        return ProviderPlaylist(reference, _text(payload.get("title")), page)

    async def track(self, reference: MediaReference) -> ProviderTrack:
        if (
            reference.provider is not ProviderName.YOUTUBE
            or reference.kind is not MediaKind.TRACK
        ):
            raise ProviderError("YouTube cannot load this track identity.")
        result = await self._execute(
            _ytdlp(
                self._node_path,
                reference.source_url,
                ("--no-playlist", "--dump-single-json"),
            )
        )
        track = _video_track(_payload(result), quality=ObservationQuality.DETAIL)
        if (
            track is None
            or track.external_id != reference.external_id
            or track.availability is SourceAvailability.UNAVAILABLE
        ):
            raise ProviderError("YouTube returned no available track.")
        return track

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(task for task in self._requests if not task.done())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, arguments: tuple[str, ...]) -> ProcessResult:
        if self._closed:
            raise ProviderError("YouTube provider is closed.")
        task = asyncio.create_task(self._runner(arguments, timeout=self._timeout))
        self._requests.add(task)
        try:
            return await task
        except ProcessTimeoutError:
            raise ProviderError(
                "YouTube took too long to respond.", retryable=True
            ) from None
        except ProcessOutputLimitError:
            raise ProviderError("YouTube returned too much metadata.") from None
        except ProcessCleanupError:
            raise ProviderError("YouTube cleanup failed.") from None
        except OSError:
            raise ProviderError("YouTube resolver could not be started.") from None
        finally:
            self._requests.discard(task)


def _worker_main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] != "search":
        raise SystemExit("Invalid YouTube worker request.")
    from ytmusicapi import YTMusic

    try:
        entries = YTMusic().search(sys.argv[2], filter="songs", limit=int(sys.argv[3]))
        sys.stdout.buffer.write(
            json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8")
        )
    except Exception:
        raise SystemExit("YouTube Music search failed.") from None


if __name__ == "__main__":
    _worker_main()
