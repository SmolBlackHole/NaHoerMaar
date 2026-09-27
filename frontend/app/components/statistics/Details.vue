<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { GroupStatistics } from "~/core/models/statistics";
import { formatStatistic, formatStatisticsDuration } from "~/core/models/statistics";

const props = defineProps<{ report: GroupStatistics }>();
const { icons } = useTheme();
const completionRate = computed(() => {
	const playback = props.report.totals.playback.overall;
	const resolved = playback.completed + playback.skipped + playback.stopped + playback.failed;
	return resolved ? Math.round((playback.completed / resolved) * 100) : 0;
});
const openPlaybacks = computed(() => {
	const playback = props.report.totals.playback.overall;
	return Math.max(
		0,
		playback.started -
			playback.completed -
			playback.skipped -
			playback.stopped -
			playback.failed,
	);
});
const outcomes = computed(() => {
	const playback = props.report.totals.playback.overall;
	return [
		{ label: "Completed", value: playback.completed },
		{ label: "Skipped", value: playback.skipped },
		{ label: "Stopped", value: playback.stopped },
		{ label: "Failed", value: playback.failed },
	];
});
const facts = computed(() => [
	{
		label: "Channel presence",
		value: formatStatisticsDuration(props.report.totals.presence_seconds),
	},
	{ label: "Different tracks", value: formatStatistic(props.report.totals.unique_tracks) },
	{ label: "Different artists", value: formatStatistic(props.report.totals.unique_artists) },
	{
		label: "Average manual wait",
		value:
			props.report.totals.average_wait_seconds == null
				? "No plays yet"
				: formatStatisticsDuration(props.report.totals.average_wait_seconds),
	},
	{
		label: "Average listeners",
		value:
			props.report.highlights.average_listeners == null
				? "No playback yet"
				: props.report.highlights.average_listeners.toLocaleString(undefined, {
						maximumFractionDigits: 1,
					}),
	},
]);
</script>

<template>
	<details class="group rounded-2xl bg-elevated/35 [&>summary::-webkit-details-marker]:hidden">
		<summary
			class="flex cursor-pointer list-none items-center justify-between gap-4 rounded-2xl px-5 py-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
		>
			<div>
				<h2 class="text-sm font-semibold text-highlighted">
					Requests, outcomes and coverage
				</h2>
				<p class="mt-1 text-xs text-muted">
					The useful detail behind the headline numbers.
				</p>
			</div>
			<UIcon
				:name="icons.chevronDown"
				class="size-5 shrink-0 text-muted transition-transform duration-200 group-open:rotate-180"
			/>
		</summary>

		<div class="grid gap-8 px-5 pb-5 pt-2 lg:grid-cols-3">
			<section aria-labelledby="request-detail-heading">
				<h3 id="request-detail-heading" class="text-sm font-semibold text-highlighted">
					Requests
				</h3>
				<dl class="mt-3 space-y-2">
					<div class="flex items-center justify-between gap-4">
						<dt class="text-sm text-muted">Manual</dt>
						<dd class="font-medium tabular-nums text-highlighted">
							{{ report.totals.requests.manual }}
						</dd>
					</div>
					<div class="flex items-center justify-between gap-4">
						<dt class="text-sm text-muted">Radio</dt>
						<dd class="font-medium tabular-nums text-highlighted">
							{{ report.totals.requests.radio }}
						</dd>
					</div>
				</dl>
			</section>

			<section aria-labelledby="outcome-detail-heading">
				<div class="flex items-baseline justify-between gap-3">
					<h3 id="outcome-detail-heading" class="text-sm font-semibold text-highlighted">
						Outcomes
					</h3>
					<p class="text-xs tabular-nums text-muted">{{ completionRate }}% completed</p>
				</div>
				<dl class="mt-3 grid grid-cols-2 gap-x-5 gap-y-2">
					<div
						v-for="item in outcomes"
						:key="item.label"
						class="flex items-center justify-between gap-3"
					>
						<dt class="text-xs text-muted">{{ item.label }}</dt>
						<dd class="text-sm font-medium tabular-nums text-highlighted">
							{{ item.value }}
						</dd>
					</div>
				</dl>
				<p v-if="openPlaybacks" class="mt-3 text-xs text-muted">
					{{ openPlaybacks }} still in progress or awaiting an outcome.
				</p>
			</section>

			<section aria-labelledby="coverage-detail-heading">
				<h3 id="coverage-detail-heading" class="text-sm font-semibold text-highlighted">
					Coverage
				</h3>
				<dl class="mt-3 space-y-2">
					<div
						v-for="fact in facts"
						:key="fact.label"
						class="flex items-center justify-between gap-4"
					>
						<dt class="text-xs text-muted">{{ fact.label }}</dt>
						<dd class="text-sm font-medium tabular-nums text-highlighted">
							{{ fact.value }}
						</dd>
					</div>
				</dl>
			</section>
		</div>
	</details>
</template>
