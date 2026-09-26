// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

export type Statistics = components["schemas"]["StatisticsView"];
export type ActivityBucket = components["schemas"]["ActivityBucketView"];

export function formatStatisticsDuration(seconds: number): string {
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	const remainder = minutes % 60;
	return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

export function formatStatistic(value: number): string {
	return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
}
