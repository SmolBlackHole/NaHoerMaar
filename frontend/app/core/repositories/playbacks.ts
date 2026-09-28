// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { PlaybackEndReason } from "../models/playbacks";

export interface PlaybackHistoryQuery {
	page?: number;
	pageSize?: number;
	query?: string;
	radio?: boolean;
	requestedBy?: string;
	startedFrom?: string;
	startedTo?: string;
	endReason?: PlaybackEndReason;
	snapshot?: string;
}

export function createPlaybacksRepository(request: Transport) {
	return {
		list: (options: PlaybackHistoryQuery = {}, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/playbacks", {
					params: {
						query: {
							page: options.page ?? 1,
							page_size: options.pageSize ?? 20,
							q: options.query,
							radio: options.radio,
							requested_by: options.requestedBy,
							started_from: options.startedFrom,
							started_to: options.startedTo,
							end_reason: options.endReason,
							snapshot: options.snapshot,
						},
					},
					signal,
				}),
			),
	};
}

export type PlaybacksRepository = ReturnType<typeof createPlaybacksRepository>;
