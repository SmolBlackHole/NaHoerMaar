// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { StatisticsPeriod } from "../models/account";
import type { Statistics } from "../models/statistics";
import { createQueryState } from "./queryState";

export function createStatisticsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const report = createQueryState<Statistics>(authority);
	return {
		report,
		overview: (period: StatisticsPeriod = "7d") =>
			report.load((signal) => client.statistics.overview(period, signal)),
		user: (userId: string, period: StatisticsPeriod = "30d") =>
			report.load((signal) => client.statistics.user(userId, period, signal)),
		dispose: report.dispose,
	};
}
