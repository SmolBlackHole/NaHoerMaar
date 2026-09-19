# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import nahormaar_backend


def test_package_imports() -> None:
    assert nahormaar_backend.__name__ == "nahormaar_backend"
