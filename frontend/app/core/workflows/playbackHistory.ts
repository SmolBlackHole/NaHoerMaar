// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { PlaybackEndReason, PlaybackHistoryPage } from "../models/playbacks";
import { createPagePagination } from "./pagePagination";

export interface PlaybackHistoryFilters {
	radio?: boolean;
	requestedBy?: string;
	startedFrom?: string;
	startedTo?: string;
	endReason?: PlaybackEndReason;
}

export function createPlaybackHistoryWorkflow(client: BackendClient, authority: SessionAuthority) {
	const pagination = createPagePagination<PlaybackHistoryPage, PlaybackHistoryFilters>(
		authority,
		(request, signal) =>
			client.playbacks.list(
				{
					page: request.page,
					pageSize: request.pageSize,
					query: request.query,
					radio: request.filters.radio,
					requestedBy: request.filters.requestedBy,
					startedFrom: request.filters.startedFrom,
					startedTo: request.filters.startedTo,
					endReason: request.filters.endReason,
					snapshot: request.snapshot,
				},
				signal,
			),
	);
	return {
		history: pagination.page,
		load: pagination.load,
		dispose: pagination.dispose,
	};
}
