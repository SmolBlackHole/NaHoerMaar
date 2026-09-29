// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { ReactionValue } from "../models/library";

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

export function createLibraryRepository(request: Transport) {
	return {
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
