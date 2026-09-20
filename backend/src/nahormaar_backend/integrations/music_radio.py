# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Public YouTube Music radio in a cancellable child process."""

import json
import sys

from ytmusicapi import YTMusic


def main() -> None:
    try:
        kind, identifier, limit = sys.argv[1:4]
        music = YTMusic()
        result = (
            music.get_watch_playlist(videoId=identifier, radio=True, limit=int(limit))
            if kind == "track"
            else music.get_watch_playlist(
                playlistId=f"RDAMPL{identifier}", limit=int(limit)
            )
        )
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    except Exception:
        sys.exit("YouTube Music radio failed.")


if __name__ == "__main__":
    main()
