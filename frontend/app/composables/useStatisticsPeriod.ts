// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { StatisticsPeriod } from "~/core/models/account";

const periods = new Set<StatisticsPeriod>(["7d", "30d", "year", "all"]);

export function useStatisticsPeriod() {
	const route = useRoute();
	const router = useRouter();
	return computed<StatisticsPeriod>({
		get: () => {
			const value = typeof route.query.period === "string" ? route.query.period : "7d";
			return periods.has(value as StatisticsPeriod) ? (value as StatisticsPeriod) : "7d";
		},
		set: (value) => {
			void router.replace({ query: { ...route.query, period: value } });
		},
	});
}
