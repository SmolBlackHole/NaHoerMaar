# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Common yt-dlp arguments for playback and discovery."""

import sys
from pathlib import Path


def ytdlp_arguments(
    node_path: Path, source: str, options: tuple[str, ...]
) -> tuple[str, ...]:
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
