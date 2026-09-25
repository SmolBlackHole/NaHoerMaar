// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { StatisticsPeriod } from "../models/account";
import type { Statistics } from "../models/statistics";

export function createStatisticsRepository(request: Transport) {
	return {
		overview: (period: StatisticsPeriod = "7d", signal?: AbortSignal) =>
			request<Statistics>(`/api/statistics/overview?period=${period}`, { signal }),
		user: (userId: string, period: StatisticsPeriod = "30d", signal?: AbortSignal) =>
			request<Statistics>(
				`/api/statistics/users/${encodeURIComponent(userId)}?period=${period}`,
				{ signal },
			),
	};
}

export type StatisticsRepository = ReturnType<typeof createStatisticsRepository>;
