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
		long_haul: "Long haul",
		queue_architect: "Queue architect",
		locked_in: "Locked in",
		dawn_patrol: "Dawn patrol",
		weekend_regular: "Weekend regular",
		taste_maker: "Taste maker",
		radio_convert: "Radio convert",
		artist_explorer: "Artist explorer",
		listening_streak: "Listening streak",
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
		long_haul: icons.value.clock,
		queue_architect: icons.value.list,
		locked_in: icons.value.headphones,
		dawn_patrol: icons.value.light,
		weekend_regular: icons.value.sunMoon,
		taste_maker: icons.value.sparkles,
		radio_convert: icons.value.reload,
		artist_explorer: icons.value.search,
		listening_streak: icons.value.clock,
	}[badge.kind];
}

function description(badge: ListenerBadge) {
	const percentage = Math.round(badge.value * 100);
	const shareOfSample =
		badge.sample_size > 0 ? Math.round((badge.value / badge.sample_size) * 100) : 0;
	return {
		night_owl: `${percentage}% of heard time landed between midnight and 5 AM, the highest eligible share. Requires 10 plays and 30 minutes heard.`,
		explorer: `${percentage}% unique tracks across ${badge.sample_size} heard tracks, the highest eligible share. Requires 10 plays and 30 minutes heard.`,
		resident_dj: `${badge.sample_size} manual requests reached confirmed playback, the highest total this period.`,
		radio_regular: `${percentage}% of ${badge.sample_size} heard tracks came from Radio, the highest eligible share. Requires 10 plays and 30 minutes heard.`,
		repeat_offender: `${percentage}% repeats across ${badge.sample_size} heard tracks, the highest eligible share. Requires 10 plays and 30 minutes heard.`,
		always_around: `${formatStatisticsDuration(badge.value)} spent in the voice channel. Earned at 2 hours.`,
		all_ears: `${formatStatisticsDuration(badge.value)} of music actually heard. Earned at 2 hours.`,
		queue_curator: `${badge.sample_size} manual requests reached confirmed playback. Earned at 10.`,
		radio_rider: `${Math.round(badge.value)} Radio tracks heard across ${badge.sample_size} plays. Earned at 20.`,
		wide_rotation: `${Math.round(badge.value)} different tracks across ${badge.sample_size} plays. Earned at 25.`,
		long_haul: `${formatStatisticsDuration(badge.value)} of music heard in this period. Earned at 6 hours.`,
		queue_architect: `${badge.sample_size} manual requests reached confirmed playback. Earned at 25.`,
		locked_in: `Music was audible for ${percentage}% of ${formatStatisticsDuration(badge.sample_size)} in the channel. Earned at 90% across at least 2 hours.`,
		dawn_patrol: `${formatStatisticsDuration(badge.value)} heard between 5 and 9 AM, ${shareOfSample}% of ${formatStatisticsDuration(badge.sample_size)} total. Earned at 1 hour and 25%.`,
		weekend_regular: `${formatStatisticsDuration(badge.value)} heard on weekends, ${shareOfSample}% of ${formatStatisticsDuration(badge.sample_size)} total. Earned at 2 hours and 50%.`,
		taste_maker: `${Math.round(badge.value)} of ${badge.sample_size} manual requests were later requested by someone else. Earned at 3 tracks.`,
		radio_convert: `${Math.round(badge.value)} Radio discoveries were later requested manually across ${badge.sample_size} Radio plays. Earned at 3 tracks.`,
		artist_explorer: `${Math.round(badge.value)} known artists heard across ${badge.sample_size} different tracks. Earned at 20 artists.`,
		listening_streak: `${Math.round(badge.value)} consecutive listening days across ${badge.sample_size} active days. Earned at 3 days.`,
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
