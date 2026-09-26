// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { RecentPlaybackPage } from "../models/listening";

export function createListeningRepository(request: Transport) {
	return {
		recent: (limit = 20, cursor?: string, signal?: AbortSignal) => {
			const params = new URLSearchParams({ limit: String(limit) });
			if (cursor) params.set("cursor", cursor);
			return request<RecentPlaybackPage>(`/api/listening/recent?${params}`, { signal });
		},
	};
}

export type ListeningRepository = ReturnType<typeof createListeningRepository>;
