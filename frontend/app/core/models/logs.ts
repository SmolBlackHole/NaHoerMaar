// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

export type LogPage = components["schemas"]["LogsView"];

export interface LogFilters {
	query?: string;
	level?: string;
	source?: string;
	actorId?: string;
	requestId?: string;
	correlationId?: string;
	causationId?: string;
}
