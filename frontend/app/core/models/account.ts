// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type AccountSession = Schema["SessionView"];
export type UserProfile = Schema["ProfilePageView"];
export type Appearance = Schema["AppearanceView"];
export type ProfileUpdate = Schema["ProfileUpdate"];
export type AppearanceUpdate = Schema["AppearanceUpdate"];
export type StatisticsPeriod = Schema["StatisticsPeriod"];
