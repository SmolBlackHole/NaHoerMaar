// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { StatisticsPeriod } from "../models/account";
import type { GroupStatistics, PersonalStatistics } from "../models/statistics";
import { createQueryState } from "./queryState";

export function createStatisticsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const groupReport = createQueryState<GroupStatistics>(authority);
	const personalReport = createQueryState<PersonalStatistics>(authority);
	return {
		groupReport,
		personalReport,
		overview: (period: StatisticsPeriod = "7d") =>
			groupReport.load((signal) => client.statistics.overview(period, signal)),
		user: (userId: string, period: StatisticsPeriod = "30d") =>
			personalReport.load((signal) => client.statistics.user(userId, period, signal)),
		dispose: () => {
			groupReport.dispose();
			personalReport.dispose();
		},
	};
}
