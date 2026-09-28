// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { LogFilters } from "../models/logs";

export interface LogsQuery extends LogFilters {
	after?: number;
	limit?: number;
}

export function createLogsRepository(request: Transport) {
	return {
		recent: (query: LogsQuery = {}, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/logs", {
					params: {
						query: {
							after: query.after,
							limit: query.limit ?? 200,
							q: query.query,
							level: query.level,
							source: query.source,
							actor_id: query.actorId,
							request_id: query.requestId,
							correlation_id: query.correlationId,
							causation_id: query.causationId,
						},
					},
					signal,
				}),
			),
	};
}

export type LogsRepository = ReturnType<typeof createLogsRepository>;
