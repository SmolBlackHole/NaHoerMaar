// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type Discovery = Schema["DiscoveryView"];
export type DiscoveryKind = Schema["DiscoveryKind"];
export type Track = Schema["TrackView"];
export type DiscoveryEntry = Schema["DiscoveryEntryView"];
