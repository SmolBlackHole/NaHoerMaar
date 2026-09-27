<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { GroupStatistics } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

type Listener = GroupStatistics["top_listeners"][number];
type Badge = Listener["badges"][number];

const props = defineProps<{
	listeners: readonly Listener[];
	totalListeningSeconds: number;
}>();
const { icons } = useTheme();
const leader = computed(() => props.listeners[0]);
const runnersUp = computed(() => props.listeners.slice(1));

function name(listener: Listener) {
	return (
		listener.display_name ??
		listener.discord_display_name ??
		listener.discord_username ??
		"Listener"
	);
}

function share(listener: Listener) {
	if (props.totalListeningSeconds <= 0) return 0;
	return Math.round((listener.listening_seconds / props.totalListeningSeconds) * 100);
}

function badgeLabel(badge: Badge) {
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

function badgeIcon(badge: Badge) {
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

function badgeDescription(badge: Badge) {
	const value = Math.round(badge.value * 100);
	return {
		night_owl: `${value}% of their heard time landed between midnight and 5 AM.`,
		explorer: `${value}% unique tracks across ${badge.sample_size} heard tracks.`,
		resident_dj: `${badge.sample_size} manual requests reached confirmed playback.`,
		radio_regular: `${value}% of ${badge.sample_size} heard tracks came from Radio.`,
		repeat_offender: `${value}% repeats across ${badge.sample_size} heard tracks.`,
		always_around: `${formatStatisticsDuration(badge.value)} spent in the voice channel during this period.`,
		all_ears: `${formatStatisticsDuration(badge.value)} of music actually heard during this period.`,
		queue_curator: `${badge.sample_size} manual requests reached confirmed playback.`,
		radio_rider: `${badge.sample_size} Radio tracks heard during this period.`,
		wide_rotation: `${badge.sample_size} different tracks heard during this period.`,
	}[badge.kind];
}
</script>

<template>
	<section aria-labelledby="listeners-heading">
		<div class="flex flex-wrap items-end justify-between gap-3">
			<div>
				<h2 id="listeners-heading" class="text-lg font-semibold text-highlighted">
					Top listeners
				</h2>
				<p class="mt-1 text-xs text-muted">Who spent the most time listening in Discord.</p>
			</div>
			<p v-if="listeners.length" class="text-xs text-muted">
				{{ listeners.length }} active
				{{ listeners.length === 1 ? "listener" : "listeners" }}
			</p>
		</div>

		<div
			v-if="leader"
			class="mt-4 grid gap-3 xl:grid-cols-[minmax(18rem,0.9fr)_minmax(0,1.1fr)]"
		>
			<NuxtLink
				:to="`/profile/${leader.user_id}`"
				class="group relative min-w-0 overflow-hidden rounded-2xl border border-default bg-linear-to-br from-primary/10 via-elevated/45 to-elevated/20 p-5 transition-colors hover:border-primary/40"
			>
				<div class="flex items-start gap-4">
					<UAvatar :src="leader.avatar_url" :alt="name(leader)" size="2xl" />
					<div class="min-w-0 flex-1">
						<p class="text-xs font-medium uppercase tracking-wider text-primary">
							Top listener
						</p>
						<p class="mt-1 truncate text-lg font-semibold text-highlighted">
							{{ name(leader) }}
						</p>
						<p class="mt-1 text-sm text-muted">
							{{ formatStatisticsDuration(leader.listening_seconds) }} heard
						</p>
					</div>
					<UIcon
						:name="icons.arrowRight"
						class="size-5 shrink-0 text-dimmed transition-transform group-hover:translate-x-0.5 group-hover:text-primary"
					/>
				</div>

				<div class="mt-5">
					<div class="flex items-center justify-between gap-4 text-xs text-muted">
						<span>Share of group time</span>
						<span class="font-semibold tabular-nums text-highlighted"
							>{{ share(leader) }}%</span
						>
					</div>
					<div class="mt-2 h-1.5 overflow-hidden rounded-full bg-default">
						<div
							class="h-full rounded-full bg-primary transition-[width]"
							:style="{ width: `${share(leader)}%` }"
						/>
					</div>
				</div>

				<div v-if="leader.badges.length" class="mt-4">
					<p
						class="mb-2 flex items-center gap-1.5 text-[0.6875rem] font-medium uppercase tracking-wider text-muted"
					>
						<UIcon :name="icons.sparkles" class="size-3.5 text-primary" />
						Achievements
					</p>
					<div class="flex flex-wrap gap-1.5">
						<UTooltip
							v-for="badge in leader.badges"
							:key="badge.kind"
							:text="badgeDescription(badge)"
						>
							<UBadge
								:label="badgeLabel(badge)"
								:icon="badgeIcon(badge)"
								color="primary"
								variant="subtle"
								size="sm"
							/>
						</UTooltip>
					</div>
				</div>
			</NuxtLink>

			<div v-if="runnersUp.length" class="grid gap-2">
				<NuxtLink
					v-for="(listener, index) in runnersUp"
					:key="listener.user_id"
					:to="`/profile/${listener.user_id}`"
					class="group flex min-w-0 items-center gap-3 rounded-xl bg-elevated/35 px-3 py-2.5 transition-colors hover:bg-elevated/65"
				>
					<span
						class="w-5 shrink-0 text-center text-xs font-semibold tabular-nums text-dimmed"
					>
						{{ index + 2 }}
					</span>
					<UAvatar :src="listener.avatar_url" :alt="name(listener)" size="md" />
					<div class="min-w-0 flex-1">
						<p class="truncate text-sm font-medium text-highlighted">
							{{ name(listener) }}
						</p>
						<p class="mt-0.5 truncate text-xs text-muted">
							{{ formatStatisticsDuration(listener.listening_seconds) }} heard
							<span aria-hidden="true"> · </span>{{ share(listener) }}%
						</p>
					</div>
					<UTooltip
						v-if="listener.badges[0]"
						:text="badgeDescription(listener.badges[0])"
					>
						<UBadge
							:label="badgeLabel(listener.badges[0])"
							:icon="badgeIcon(listener.badges[0])"
							color="primary"
							variant="subtle"
							size="sm"
							class="hidden shrink-0 sm:inline-flex"
						/>
					</UTooltip>
					<span
						v-if="listener.badges.length > 1"
						class="hidden shrink-0 text-xs text-muted sm:inline"
					>
						+{{ listener.badges.length - 1 }}
					</span>
					<UIcon
						:name="icons.arrowRight"
						class="size-4 shrink-0 text-dimmed transition-transform group-hover:translate-x-0.5"
					/>
				</NuxtLink>
			</div>
		</div>
		<p v-else class="mt-4 text-sm text-muted">
			Listening time appears once people join the channel.
		</p>
	</section>
</template>
