# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from datetime import date
from pathlib import Path

import pytest

from nahoermaar.integrations.discord import load_quotes, quote_for_day


def test_daily_quote_is_stable_for_one_day(tmp_path: Path) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text(
        '[quotes]\nde = ["Eins", "Zwei"]\nnl = ["Drie"]\n',
        encoding="utf-8",
    )
    quotes = load_quotes(path)

    assert quotes == ("Eins", "Zwei", "Drie")
    assert quote_for_day(quotes, date(2026, 9, 25)) == quote_for_day(
        quotes,
        date(2026, 9, 25),
    )


def test_quotes_reject_empty_language_groups(tmp_path: Path) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text("[quotes]\nde = []\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"quotes\.de"):
        load_quotes(path)
