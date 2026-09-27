// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { PlaybackHistoryPage } from "../models/listening";

export interface PlaybackHistoryQuery {
	page?: number;
	pageSize?: number;
	query?: string;
	radio?: boolean;
	requestedBy?: string;
	snapshot?: string;
}

export function createListeningRepository(request: Transport) {
	return {
		recent: (options: PlaybackHistoryQuery = {}, signal?: AbortSignal) => {
			const params = new URLSearchParams({
				page: String(options.page ?? 1),
				page_size: String(options.pageSize ?? 20),
			});
			if (options.query) params.set("q", options.query);
			if (options.radio !== undefined) params.set("radio", String(options.radio));
			if (options.requestedBy) params.set("requested_by", options.requestedBy);
			if (options.snapshot) params.set("snapshot", options.snapshot);
			return request<PlaybackHistoryPage>(`/api/listening/recent?${params}`, { signal });
		},
	};
}

export type ListeningRepository = ReturnType<typeof createListeningRepository>;
