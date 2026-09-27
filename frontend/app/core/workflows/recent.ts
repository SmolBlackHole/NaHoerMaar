// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { PlaybackHistoryPage } from "../models/listening";
import { createPagePagination } from "./pagePagination";

export interface PlaybackHistoryFilters {
	radio?: boolean;
	requestedBy?: string;
}

export function createRecentWorkflow(client: BackendClient, authority: SessionAuthority) {
	const pagination = createPagePagination<PlaybackHistoryPage, PlaybackHistoryFilters>(
		authority,
		(request, signal) =>
			client.listening.recent(
				{
					page: request.page,
					pageSize: request.pageSize,
					query: request.query,
					radio: request.filters.radio,
					requestedBy: request.filters.requestedBy,
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
