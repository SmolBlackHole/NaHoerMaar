// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

export type IncidentReport = components["schemas"]["IncidentReportView"];
export type IncidentPeriod = components["schemas"]["IncidentPeriod"];
export type IncidentSeverity = components["schemas"]["IncidentSeverity"];

export interface IncidentFilters {
	period: IncidentPeriod;
	severity?: IncidentSeverity;
	component?: string;
	code?: string;
	actorId?: string;
}
