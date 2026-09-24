# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Start the NaHörMaar backend."""

import uvicorn


def main() -> None:
    """Run the FastAPI factory on the container-internal backend port."""
    uvicorn.run(
        "nahoermaar.api.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container service boundary
        port=8000,
    )


if __name__ == "__main__":
    main()
