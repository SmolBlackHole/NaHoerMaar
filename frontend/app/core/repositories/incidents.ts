// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { IncidentPeriod, IncidentReport } from "../models/incidents";

export function createIncidentsRepository(request: Transport) {
	return {
		report: (period: IncidentPeriod, signal?: AbortSignal) =>
			request<IncidentReport>(`/api/incidents?period=${period}`, { signal }),
	};
}

export type IncidentsRepository = ReturnType<typeof createIncidentsRepository>;
