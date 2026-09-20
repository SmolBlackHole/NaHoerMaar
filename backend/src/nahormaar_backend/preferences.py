# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Validated appearance choices saved with each listener account."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Appearance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal["light", "dark", "system", "time"] = "dark"
    artworkColors: bool = True
    primaryColor: Literal[
        "red",
        "orange",
        "amber",
        "yellow",
        "lime",
        "green",
        "emerald",
        "teal",
        "cyan",
        "sky",
        "blue",
        "indigo",
        "violet",
        "purple",
        "fuchsia",
        "pink",
        "rose",
    ] = "teal"
    neutralColor: Literal["slate", "gray", "zinc", "neutral", "stone"] = "zinc"
    fontFamily: Literal[
        "Public Sans",
        "DM Sans",
        "Geist",
        "Inter",
        "Poppins",
        "Outfit",
        "Raleway",
    ] = "Geist"
    iconSet: Literal["lucide", "ph", "heroicons", "tabler"] = "lucide"
    textSize: Literal["sm", "md", "lg"] = "md"
