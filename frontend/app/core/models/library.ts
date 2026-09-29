// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type LibraryTrack = Schema["LibraryTrackView"];
export type LibraryTrackPage = Schema["LibraryTrackPageView"];
export type ReactionParticipant = Schema["ReactionParticipantView"];
export type ReactionParticipantPage = Schema["ReactionParticipantPageView"];
export type ReactionSummary = Schema["ReactionSummaryView"];
export type ReactionValue = Schema["ReactionValue"];
