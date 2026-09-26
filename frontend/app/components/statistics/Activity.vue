<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ActivityBucket } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

const props = withDefaults(
	defineProps<{
		activity?: readonly ActivityBucket[];
		loading?: boolean;
		granularity?: ActivityBucket["granularity"];
	}>(),
	{ activity: () => [], loading: false, granularity: "day" },
);
const maxPlays = computed(() => Math.max(1, ...props.activity.map(({ plays }) => plays)));
const yTicks = computed(() => {
	const step = Math.max(1, Math.ceil(maxPlays.value / 3));
	return [step * 3, step * 2, step, 0];
});
const heading = computed(() =>
	(props.activity[0]?.granularity ?? props.granularity) === "month"
		? "Monthly activity"
		: "Daily activity",
);

function label(value: string, granularity: ActivityBucket["granularity"]) {
	return new Intl.DateTimeFormat(
		undefined,
		granularity === "month"
			? { month: "short", year: "numeric", timeZone: "UTC" }
			: { month: "short", day: "numeric", timeZone: "UTC" },
	).format(new Date(`${value}T00:00:00Z`));
}
</script>

<template>
	<section class="min-w-0" aria-labelledby="statistics-activity-heading">
		<h2 id="statistics-activity-heading" class="text-lg font-semibold text-highlighted">
			{{ heading }}
		</h2>
		<p class="mt-1 text-xs text-muted">Confirmed playback starts in the selected period.</p>
		<figure v-if="loading" class="mt-8" aria-hidden="true">
			<div class="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2">
				<div class="flex h-44 flex-col justify-between pb-px">
					<USkeleton v-for="index in 4" :key="index" class="ml-auto h-3 w-6" />
				</div>
				<div class="relative flex h-44 items-end gap-1 border-b border-default sm:gap-2">
					<div
						v-for="line in 3"
						:key="line"
						class="pointer-events-none absolute inset-x-0 border-t border-default/60"
						:style="{ top: `${((line - 1) / 3) * 100}%` }"
					/>
					<USkeleton
						v-for="(height, index) in [44, 72, 36, 84, 58, 68, 92]"
						:key="index"
						class="min-w-0 flex-1 rounded-b-none rounded-t-sm"
						:style="{ height: `${height}%` }"
					/>
				</div>
			</div>
			<figcaption class="mt-3 flex justify-between">
				<USkeleton class="h-3 w-16" />
				<USkeleton class="h-3 w-16" />
			</figcaption>
		</figure>
		<figure v-else-if="activity.length" class="mt-8">
			<div class="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2">
				<div
					class="flex h-44 flex-col justify-between pb-px text-right text-[0.6875rem] tabular-nums text-muted"
					aria-label="Plays"
				>
					<span v-for="tick in yTicks" :key="tick">{{ tick }}</span>
				</div>
				<div class="relative flex h-44 items-end gap-1 border-b border-default sm:gap-2">
					<div
						v-for="line in 3"
						:key="line"
						class="pointer-events-none absolute inset-x-0 border-t border-default/60"
						:style="{ top: `${((line - 1) / 3) * 100}%` }"
					/>
					<UTooltip
						v-for="bucket in activity"
						:key="bucket.started_on"
						:text="`${label(bucket.started_on, bucket.granularity)}: ${bucket.plays} plays, ${formatStatisticsDuration(bucket.listening_seconds)} heard, ${formatStatisticsDuration(bucket.presence_seconds)} present`"
					>
						<div class="relative flex h-full min-w-0 flex-1 items-end">
							<div
								class="w-full rounded-t-sm bg-primary/75"
								:style="{
									height: `${Math.max(bucket.plays ? 5 : 0, (bucket.plays / yTicks[0]!) * 100)}%`,
								}"
							/>
						</div>
					</UTooltip>
				</div>
			</div>
			<figcaption class="mt-3 flex justify-between text-xs text-muted">
				<span>{{ label(activity[0]!.started_on, activity[0]!.granularity) }}</span>
				<span>{{ label(activity.at(-1)!.started_on, activity.at(-1)!.granularity) }}</span>
			</figcaption>
		</figure>
		<p v-else class="mt-8 text-sm text-muted">No confirmed playback in this period.</p>
	</section>
</template>
