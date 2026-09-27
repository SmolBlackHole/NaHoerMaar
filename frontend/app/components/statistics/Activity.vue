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
		scope?: "group" | "personal";
	}>(),
	{ activity: () => [], loading: false, granularity: "day", scope: "group" },
);

type Measure = "playback_seconds" | "listening_seconds" | "presence_seconds";

const groupMeasures = [
	{
		value: "playback_seconds" as const,
		label: "Playback time",
		description: "Shared time with audible playback.",
	},
	{
		value: "listening_seconds" as const,
		label: "Time heard",
		description: "Combined audible time across listeners.",
	},
	{
		value: "presence_seconds" as const,
		label: "Channel presence",
		description: "Combined time listeners spent in the voice channel.",
	},
] satisfies readonly { value: Measure; label: string; description: string }[];

const personalMeasures = [
	{
		value: "listening_seconds" as const,
		label: "Time heard",
		description: "Audible time credited to this listener.",
	},
	{
		value: "presence_seconds" as const,
		label: "Channel presence",
		description: "Time this listener spent in the voice channel.",
	},
] satisfies readonly { value: Measure; label: string; description: string }[];

const measures = computed(() => (props.scope === "personal" ? personalMeasures : groupMeasures));
const measure = ref<Measure>(props.scope === "personal" ? "listening_seconds" : "playback_seconds");
const selectedMeasure = computed(
	() => measures.value.find((item) => item.value === measure.value)!,
);
const maxSeconds = computed(() =>
	Math.max(1, ...props.activity.map((bucket) => bucket[measure.value])),
);
const yTicks = computed(() => {
	const maximum = maxSeconds.value;
	return [maximum, maximum * (2 / 3), maximum / 3, 0];
});
const heading = computed(() =>
	(props.activity[0]?.granularity ?? props.granularity) === "month"
		? "Monthly activity"
		: "Daily activity",
);

watch(
	() => props.scope,
	(scope) => {
		measure.value = scope === "personal" ? "listening_seconds" : "playback_seconds";
	},
);

function label(value: string, granularity: ActivityBucket["granularity"]) {
	return new Intl.DateTimeFormat(
		undefined,
		granularity === "month"
			? { month: "short", year: "numeric", timeZone: "UTC" }
			: { month: "short", day: "numeric", timeZone: "UTC" },
	).format(new Date(`${value}T00:00:00Z`));
}

function durationLabel(seconds: number) {
	if (seconds < 60) return `${Math.round(seconds)}s`;
	return formatStatisticsDuration(seconds);
}
</script>

<template>
	<section class="min-w-0" aria-labelledby="statistics-activity-heading">
		<div class="flex flex-wrap items-end justify-between gap-4">
			<div>
				<h2 id="statistics-activity-heading" class="text-lg font-semibold text-highlighted">
					{{ heading }}
				</h2>
				<p class="mt-1 text-xs text-muted">{{ selectedMeasure.description }}</p>
			</div>
			<div class="flex flex-wrap gap-1" role="group" aria-label="Activity measure">
				<UButton
					v-for="item in measures"
					:key="item.value"
					:label="item.label"
					:color="measure === item.value ? 'primary' : 'neutral'"
					:variant="measure === item.value ? 'soft' : 'ghost'"
					size="xs"
					:aria-pressed="measure === item.value"
					@click="measure = item.value"
				/>
			</div>
		</div>
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
					aria-label="Duration"
				>
					<span v-for="tick in yTicks" :key="tick">{{ durationLabel(tick) }}</span>
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
						:text="`${label(bucket.started_on, bucket.granularity)}: ${durationLabel(bucket.playback_seconds)} playback, ${durationLabel(bucket.listening_seconds)} heard, ${durationLabel(bucket.presence_seconds)} present, ${bucket.plays} confirmed plays`"
					>
						<div class="relative flex h-full min-w-0 flex-1 items-end">
							<div
								class="w-full rounded-t-sm bg-primary/75"
								:style="{
									height: `${Math.max(bucket[measure] ? 5 : 0, (bucket[measure] / yTicks[0]!) * 100)}%`,
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
		<p v-else class="mt-8 text-sm text-muted">No listening activity in this period.</p>
	</section>
</template>
