// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { IncidentFilters } from "../models/incidents";

export interface IncidentReportQuery extends IncidentFilters {
	page?: number;
	pageSize?: number;
}

export function createIncidentsRepository(request: Transport) {
	return {
		report: (query: IncidentReportQuery, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/incidents", {
					params: {
						query: {
							period: query.period,
							severity: query.severity,
							component: query.component,
							code: query.code,
							actor_id: query.actorId,
							page: query.page,
							page_size: query.pageSize,
						},
					},
					signal,
				}),
			),
	};
}

export type IncidentsRepository = ReturnType<typeof createIncidentsRepository>;
