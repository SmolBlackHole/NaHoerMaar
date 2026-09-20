# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Run public YouTube Music discovery in a cancellable worker process."""

import json
import sys

from ytmusicapi import YTMusic


def main() -> None:
    try:
        entries = YTMusic().search(sys.argv[1], filter="songs", limit=int(sys.argv[2]))
        payload = json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(payload)
    except Exception:
        sys.exit("YouTube Music search failed.")


if __name__ == "__main__":
    main()
