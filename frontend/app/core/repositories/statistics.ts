// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { StatisticsPeriod } from "../models/account";
import type { GroupStatistics } from "../models/statistics";

export function createStatisticsRepository(request: Transport) {
	return {
		get: (period: StatisticsPeriod = "7d", signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/statistics", {
					params: { query: { period } },
					signal,
				}),
			),
	};
}

export type StatisticsRepository = ReturnType<typeof createStatisticsRepository>;
