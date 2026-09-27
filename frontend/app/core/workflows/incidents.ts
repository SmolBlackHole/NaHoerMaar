// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { IncidentPeriod, IncidentReport } from "../models/incidents";
import { createQueryState } from "./queryState";

export function createIncidentsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const report = createQueryState<IncidentReport>(authority);

	function load(period: IncidentPeriod) {
		return report.load((signal) => client.incidents.report(period, signal));
	}

	return { report, load, dispose: report.dispose };
}
