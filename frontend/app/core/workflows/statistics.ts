// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { StatisticsPeriod } from "../models/account";
import type { GroupStatistics } from "../models/statistics";
import { createQueryState } from "./queryState";

export function createStatisticsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const groupReport = createQueryState<GroupStatistics>(authority);
	return {
		groupReport,
		load: (period: StatisticsPeriod = "7d") =>
			groupReport.load((signal) => client.statistics.get(period, signal)),
		dispose: () => groupReport.dispose(),
	};
}
