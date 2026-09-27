<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ListenerBadge } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

withDefaults(
	defineProps<{
		badges: readonly ListenerBadge[];
		heading?: boolean;
		periodLabel?: string;
	}>(),
	{ heading: false, periodLabel: undefined },
);

const { icons } = useTheme();

function label(badge: ListenerBadge) {
	return {
		night_owl: "Night owl",
		explorer: "Explorer",
		resident_dj: "Resident DJ",
		radio_regular: "Radio regular",
		repeat_offender: "Repeat offender",
		always_around: "Always around",
		all_ears: "All ears",
		queue_curator: "Queue curator",
		radio_rider: "Radio rider",
		wide_rotation: "Wide rotation",
	}[badge.kind];
}

function icon(badge: ListenerBadge) {
	return {
		night_owl: icons.value.dark,
		explorer: icons.value.search,
		resident_dj: icons.value.music,
		radio_regular: icons.value.radio,
		repeat_offender: icons.value.reload,
		always_around: icons.value.clock,
		all_ears: icons.value.headphones,
		queue_curator: icons.value.list,
		radio_rider: icons.value.radio,
		wide_rotation: icons.value.music,
	}[badge.kind];
}

function description(badge: ListenerBadge) {
	const percentage = Math.round(badge.value * 100);
	return {
		night_owl: `${percentage}% of heard time landed between midnight and 5 AM.`,
		explorer: `${percentage}% unique tracks across ${badge.sample_size} heard tracks.`,
		resident_dj: `${badge.sample_size} manual requests reached confirmed playback.`,
		radio_regular: `${percentage}% of ${badge.sample_size} heard tracks came from Radio.`,
		repeat_offender: `${percentage}% repeats across ${badge.sample_size} heard tracks.`,
		always_around: `${formatStatisticsDuration(badge.value)} spent in the voice channel.`,
		all_ears: `${formatStatisticsDuration(badge.value)} of music actually heard.`,
		queue_curator: `${badge.sample_size} manual requests reached confirmed playback.`,
		radio_rider: `${badge.sample_size} Radio tracks heard.`,
		wide_rotation: `${badge.sample_size} different tracks heard.`,
	}[badge.kind];
}
</script>

<template>
	<div v-if="badges.length">
		<div v-if="heading" class="mb-3 flex flex-wrap items-center justify-between gap-2">
			<p
				class="flex items-center gap-1.5 text-[0.6875rem] font-medium uppercase tracking-wider text-muted"
			>
				<UIcon :name="icons.sparkles" class="size-3.5 text-primary" />
				Achievements
			</p>
			<p v-if="periodLabel" class="text-xs text-muted">{{ periodLabel }}</p>
		</div>
		<div class="flex flex-wrap gap-1.5">
			<UTooltip v-for="badge in badges" :key="badge.kind" :text="description(badge)">
				<UBadge
					:label="label(badge)"
					:icon="icon(badge)"
					color="primary"
					variant="subtle"
					size="sm"
				/>
			</UTooltip>
		</div>
	</div>
</template>
