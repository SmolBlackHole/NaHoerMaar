// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { IncidentFilters, IncidentReport } from "../models/incidents";
import { createPagePagination } from "./pagePagination";

export function createIncidentsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const pagination = createPagePagination<IncidentReport, IncidentFilters>(
		authority,
		(request, signal) =>
			client.incidents.report(
				{
					...request.filters,
					page: request.page,
					pageSize: request.pageSize,
					snapshot: request.snapshot,
				},
				signal,
			),
	);

	return { report: pagination.page, load: pagination.load, dispose: pagination.dispose };
}
