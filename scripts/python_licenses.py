# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Collect installed Python notices and the selected FFmpeg build's license."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

from dotenv import dotenv_values

from nahormaar_backend.config import ffmpeg_executable

ROOT = Path(__file__).resolve().parents[1]


def collect() -> list[dict[str, str]]:
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "piplicenses",
            "--format=json",
            "--with-license-file",
            "--no-license-path",
            "--with-notice-file",
            "--with-urls",
            "--ignore-packages",
            "nahormaar-backend",
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    packages: list[dict[str, str]] = []
    for item in json.loads(result.stdout):
        texts = [item.get("LicenseText"), item.get("NoticeText")]
        packages.append(
            {
                "name": item["Name"],
                "version": item["Version"],
                "license": item["License"],
                "category": "Python",
                "url": item["URL"],
                "text": "\n\n".join(t for t in texts if t and t != "UNKNOWN"),
            }
        )

    binary = ffmpeg_executable(
        os.environ.get("FFMPEG_PATH") or dotenv_values(ROOT / ".env").get("FFMPEG_PATH")
    )
    ffmpeg = subprocess.run(  # noqa: S603
        [str(binary), "-L"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    # FFmpeg writes its build/version to stderr and its license to stdout.
    version = (ffmpeg.stderr + ffmpeg.stdout).splitlines()[0]
    packages.append(
        {
            "name": "FFmpeg",
            "version": version.removeprefix("ffmpeg version ").split(" Copyright")[0],
            "license": "Build-specific license",
            "category": "Audio",
            "url": "https://ffmpeg.org/legal.html",
            "text": ffmpeg.stdout,
        }
    )
    opus_notice = Path(
        str(distribution("discord.py").locate_file("discord/bin/COPYING"))
    )
    if opus_notice.is_file():
        packages.append(
            {
                "name": "Opus (bundled with discord.py)",
                "version": "",
                "license": "BSD-3-Clause",
                "category": "Audio",
                "url": "https://opus-codec.org/license/",
                "text": opus_notice.read_text(encoding="utf-8"),
            }
        )
    return packages


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
