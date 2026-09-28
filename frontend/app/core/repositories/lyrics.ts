// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";

export function createLyricsRepository(request: Transport) {
	return {
		get: (trackId: string, refresh = false, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/tracks/{track_id}/lyrics", {
					params: {
						path: { track_id: trackId },
						query: refresh ? { refresh: true } : undefined,
					},
					signal,
				}),
			),
	};
}

export type LyricsRepository = ReturnType<typeof createLyricsRepository>;
