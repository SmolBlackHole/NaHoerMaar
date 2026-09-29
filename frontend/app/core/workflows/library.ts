// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type {
	LibraryTrackPage,
	PlaylistEntryPage,
	PlaylistPage,
	ReactionParticipantPage,
	ReactionValue,
} from "../models/library";
import { createPagePagination } from "./pagePagination";

export interface LibraryTrackFilters {
	reaction: ReactionValue;
}

export interface ReactionParticipantFilters {
	reaction: ReactionValue;
}

export function createLibraryWorkflow(client: BackendClient, authority: SessionAuthority) {
	let participantTrackId: string | null = null;
	let entryPlaylistId: string | null = null;
	const tracks = createPagePagination<LibraryTrackPage, LibraryTrackFilters>(
		authority,
		(request, signal) =>
			client.library.tracks(
				{
					page: request.page,
					pageSize: request.pageSize,
					query: request.query,
					reaction: request.filters.reaction,
					snapshot: request.snapshot,
				},
				signal,
			),
	);
	const participants = createPagePagination<ReactionParticipantPage, ReactionParticipantFilters>(
		authority,
		(request, signal) => {
			const trackId = participantTrackId;
			if (!trackId) throw new Error("A track is required before loading reactions.");
			return client.library.participants(
				trackId,
				{
					page: request.page,
					pageSize: request.pageSize,
					reaction: request.filters.reaction,
					snapshot: request.snapshot,
				},
				signal,
			);
		},
	);
	const playlists = createPagePagination<PlaylistPage>(authority, (request, signal) =>
		client.library.playlists(
			{
				page: request.page,
				pageSize: request.pageSize,
				query: request.query,
				snapshot: request.snapshot,
			},
			signal,
		),
	);
	const entries = createPagePagination<PlaylistEntryPage>(authority, (request, signal) => {
		const playlistId = entryPlaylistId;
		if (!playlistId) throw new Error("A playlist is required before loading entries.");
		return client.library.playlistEntries(
			playlistId,
			{
				page: request.page,
				pageSize: request.pageSize,
				query: request.query,
				snapshot: request.snapshot,
			},
			signal,
		);
	});
	return {
		tracks: tracks.page,
		loadTracks: tracks.load,
		participants: participants.page,
		loadParticipants: (
			trackId: string,
			options: {
				page?: number;
				pageSize?: number;
				reaction: ReactionValue;
				newSnapshot?: boolean;
			},
		) => {
			const changedTrack = participantTrackId !== trackId;
			participantTrackId = trackId;
			return participants.load({
				page: options.page,
				pageSize: options.pageSize,
				filters: { reaction: options.reaction },
				newSnapshot: options.newSnapshot || changedTrack,
			});
		},
		clearParticipants: () => {
			participantTrackId = null;
			participants.page.set(null);
		},
		playlists: playlists.page,
		loadPlaylists: playlists.load,
		entries: entries.page,
		loadEntries: (
			playlistId: string,
			options: {
				page?: number;
				pageSize?: number;
				query?: string;
				newSnapshot?: boolean;
			} = {},
		) => {
			const changedPlaylist = entryPlaylistId !== playlistId;
			entryPlaylistId = playlistId;
			return entries.load({
				...options,
				newSnapshot: options.newSnapshot || changedPlaylist,
			});
		},
		clearEntries: () => {
			entryPlaylistId = null;
			entries.page.set(null);
		},
		dispose: () => {
			tracks.dispose();
			participants.dispose();
			playlists.dispose();
			entries.dispose();
		},
	};
}
