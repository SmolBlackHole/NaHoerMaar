// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type AccessState = Schema["AccessView"];
export type AccessEvent = Schema["AccessEventView"];
export type DiscordMembers = Schema["DiscordMembersView"];
