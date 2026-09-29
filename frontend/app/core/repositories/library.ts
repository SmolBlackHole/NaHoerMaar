// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { PlaylistTrack, ReactionValue } from "../models/library";

export interface LibraryTracksQuery {
	page?: number;
	pageSize?: number;
	query?: string;
	reaction?: ReactionValue;
	snapshot?: string;
}

export interface ReactionParticipantsQuery {
	page?: number;
	pageSize?: number;
	reaction?: ReactionValue;
	snapshot?: string;
}

export interface PlaylistPageQuery {
	page?: number;
	pageSize?: number;
	query?: string;
	snapshot?: string;
}

const idempotencyHeader = (operationId: string) => ({ "Idempotency-Key": operationId });

export function createLibraryRepository(request: Transport) {
	return {
		playlists: (options: PlaylistPageQuery = {}, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/library/playlists", {
					params: {
						query: {
							page: options.page ?? 1,
							page_size: options.pageSize ?? 20,
							q: options.query,
							snapshot: options.snapshot,
						},
					},
					signal,
				}),
			),
		playlist: (playlistId: string, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/library/playlists/{playlist_id}", {
					params: { path: { playlist_id: playlistId } },
					signal,
				}),
			),
		createPlaylist: (name: string, signal?: AbortSignal) =>
			request((api) => api.POST("/api/library/playlists", { body: { name }, signal })),
		importPlaylist: (sourceUrl: string, name?: string, signal?: AbortSignal) =>
			request((api) =>
				api.POST("/api/library/playlists/imports", {
					body: { source_url: sourceUrl, name },
					signal,
				}),
			),
		renamePlaylist: (
			playlistId: string,
			name: string,
			expectedRevision: number,
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.PATCH("/api/library/playlists/{playlist_id}", {
					params: { path: { playlist_id: playlistId } },
					body: { name, expected_revision: expectedRevision },
					signal,
				}),
			),
		deletePlaylist: (playlistId: string, expectedRevision: number, signal?: AbortSignal) =>
			request((api) =>
				api.DELETE("/api/library/playlists/{playlist_id}", {
					params: { path: { playlist_id: playlistId } },
					body: { expected_revision: expectedRevision },
					signal,
				}),
			),
		detachPlaylistSource: (
			playlistId: string,
			expectedRevision: number,
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.DELETE("/api/library/playlists/{playlist_id}/source", {
					params: { path: { playlist_id: playlistId } },
					body: { expected_revision: expectedRevision },
					signal,
				}),
			),
		duplicatePlaylist: (
			playlistId: string,
			expectedRevision: number,
			name?: string,
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.POST("/api/library/playlists/{playlist_id}/duplicate", {
					params: { path: { playlist_id: playlistId } },
					body: { expected_revision: expectedRevision, name },
					signal,
				}),
			),
		playlistEntries: (
			playlistId: string,
			options: PlaylistPageQuery = {},
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.GET("/api/library/playlists/{playlist_id}/entries", {
					params: {
						path: { playlist_id: playlistId },
						query: {
							page: options.page ?? 1,
							page_size: options.pageSize ?? 20,
							q: options.query,
							snapshot: options.snapshot,
						},
					},
					signal,
				}),
			),
		addPlaylistEntries: (
			playlistId: string,
			tracks: PlaylistTrack[],
			expectedRevision: number,
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.POST("/api/library/playlists/{playlist_id}/entries", {
					params: { path: { playlist_id: playlistId } },
					body: { tracks, expected_revision: expectedRevision },
					signal,
				}),
			),
		deletePlaylistEntry: (
			playlistId: string,
			entryId: string,
			expectedRevision: number,
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.DELETE("/api/library/playlists/{playlist_id}/entries/{entry_id}", {
					params: { path: { playlist_id: playlistId, entry_id: entryId } },
					body: { expected_revision: expectedRevision },
					signal,
				}),
			),
		movePlaylistEntry: (
			playlistId: string,
			entryId: string,
			position: number,
			expectedRevision: number,
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.PUT("/api/library/playlists/{playlist_id}/entries/{entry_id}/position", {
					params: { path: { playlist_id: playlistId, entry_id: entryId } },
					body: {
						position,
						expected_revision: expectedRevision,
					},
					signal,
				}),
			),
		queuePlaylist: (
			playlistId: string,
			expectedRevision: number,
			operationId: string,
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.POST("/api/library/playlists/{playlist_id}/queue", {
					params: {
						path: { playlist_id: playlistId },
						header: idempotencyHeader(operationId),
					},
					body: { expected_revision: expectedRevision },
					signal,
				}),
			),
		tracks: (options: LibraryTracksQuery = {}, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/library/tracks", {
					params: {
						query: {
							page: options.page ?? 1,
							page_size: options.pageSize ?? 20,
							q: options.query,
							reaction: options.reaction,
							snapshot: options.snapshot,
						},
					},
					signal,
				}),
			),
		summaries: (trackIds: string[], signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/library/reactions", {
					params: { query: { track_id: trackIds } },
					signal,
				}),
			),
		setReaction: (trackId: string, value: ReactionValue, signal?: AbortSignal) =>
			request((api) =>
				api.PUT("/api/library/tracks/{track_id}/reaction", {
					params: { path: { track_id: trackId } },
					body: { value },
					signal,
				}),
			),
		deleteReaction: (trackId: string, signal?: AbortSignal) =>
			request((api) =>
				api.DELETE("/api/library/tracks/{track_id}/reaction", {
					params: { path: { track_id: trackId } },
					signal,
				}),
			),
		participants: (
			trackId: string,
			options: ReactionParticipantsQuery = {},
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.GET("/api/library/tracks/{track_id}/reactions", {
					params: {
						path: { track_id: trackId },
						query: {
							page: options.page ?? 1,
							page_size: options.pageSize ?? 20,
							reaction: options.reaction,
							snapshot: options.snapshot,
						},
					},
					signal,
				}),
			),
	};
}

export type LibraryRepository = ReturnType<typeof createLibraryRepository>;
