# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Start the NaHörMaar backend."""

from .bootstrap import bootstrap


def main() -> None:
    """Build the application from its configured dependencies."""
    bootstrap()


if __name__ == "__main__":
    main()
