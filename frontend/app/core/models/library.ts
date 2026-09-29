// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type LibraryTrack = Schema["LibraryTrackView"];
export type LibraryTrackPage = Schema["LibraryTrackPageView"];
export type LibraryContributor = Schema["nahoermaar__api__library__ContributorView"];
export type LibraryContributors = Schema["ContributorsView"];
export type Playlist = Schema["PlaylistView"];
export type PlaylistScope = Schema["PlaylistScope"];
export type PlaylistVisibility = Schema["PlaylistVisibility"];
export type PlaylistPage = Schema["PlaylistPageView"];
export type PlaylistEntry = Schema["PlaylistEntryView"];
export type PlaylistEntryDeletion = Schema["PlaylistEntryDeletionView"];
export type PlaylistEntryPage = Schema["PlaylistEntryPageView"];
export type PlaylistTrack = Schema["PlaylistTrackInput"];
export type ReactionParticipant = Schema["ReactionParticipantView"];
export type ReactionParticipantPage = Schema["ReactionParticipantPageView"];
export type ReactionSummary = Schema["ReactionSummaryView"];
export type ReactionValue = Schema["ReactionValue"];
