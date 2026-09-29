<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ListenerProfile, StatisticsPeriod } from "~/core/models/account";
import { formatStatistic, formatStatisticsDuration } from "~/core/models/statistics";

const props = defineProps<{ value: ListenerProfile }>();
const period = defineModel<StatisticsPeriod>("period", { required: true });
const { icons } = useTheme();

const displayName = computed(
	() =>
		props.value.profile.display_name ??
		props.value.discord.display_name ??
		props.value.discord.username ??
		"Listener",
);
const role = computed(() => {
	if (props.value.role === "owner") return "Owner";
	if (props.value.role === "admin") return "Admin";
	return "Listener";
});
const discordAvatar = computed(() => props.value.discord.avatar_url ?? undefined);
const groupShare = computed(() => props.value.statistics.highlights.group_listening_share);
const metrics = computed(() => {
	const totals = props.value.statistics.totals;
	return [
		{
			label: "Time heard",
			value: formatStatisticsDuration(totals.listening_seconds),
			icon: icons.value.headphones,
		},
		{
			label: "Share of group time",
			value:
				groupShare.value === null
					? "No group time"
					: `${Math.round(groupShare.value * 100)}%`,
			icon: icons.value.users,
		},
		{
			label: "Channel presence",
			value: formatStatisticsDuration(totals.presence_seconds),
			icon: icons.value.clock,
		},
		{
			label: "Tracks heard",
			value: formatStatistic(totals.unique_tracks),
			icon: icons.value.music,
		},
	];
});
const recentTracks = computed(() => props.value.recent_tracks.slice(0, 8));
const periodLabel = computed(
	() =>
		({
			"7d": "Last 7 days",
			"30d": "Last 30 days",
			year: "This year",
			all: "All recorded time",
		})[period.value],
);

function formatDate(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}
</script>

<template>
	<div>
		<header class="flex flex-wrap items-center justify-between gap-5 pb-2">
			<div class="flex min-w-0 items-center gap-5">
				<UAvatar
					:src="discordAvatar"
					:alt="value.discord.display_name ?? value.discord.username ?? 'Discord account'"
					size="3xl"
				/>
				<div class="min-w-0">
					<div class="flex flex-wrap items-center gap-2.5">
						<h1 class="truncate text-2xl font-semibold text-highlighted sm:text-3xl">
							{{ displayName }}
						</h1>
						<UBadge :label="role" color="primary" variant="subtle" />
					</div>
					<p class="mt-1.5 truncate text-sm text-muted">
						@{{ value.discord.username ?? value.discord.id }}
					</p>
				</div>
			</div>
			<StatisticsPeriodSelect v-model="period" />
		</header>

		<StatisticsMetricGrid class="mt-7" :items="metrics" />
		<p v-if="value.statistics.coverage.partial" class="mt-3 text-xs leading-relaxed text-muted">
			Showing the activity NaHörMaar has recorded so far. Earlier listening is not included.
		</p>

		<slot name="details" />

		<section
			v-if="value.statistics.highlights.badges.length"
			class="mt-10 rounded-2xl bg-linear-to-br from-primary/10 via-elevated/35 to-elevated/20 p-5"
			aria-label="Achievements"
		>
			<StatisticsListenerBadges
				:badges="value.statistics.highlights.badges"
				:period-label="periodLabel"
				heading
			/>
		</section>

		<div class="mt-12 grid gap-12 2xl:grid-cols-[minmax(0,1.15fr)_minmax(24rem,0.85fr)]">
			<StatisticsActivity
				:activity="value.statistics.activity"
				:granularity="value.statistics.coverage.granularity"
				scope="personal"
			/>
			<ProfileListeningRhythm :pattern="value.statistics.highlights.listening_pattern" />
		</div>

		<ProfileFavorites class="mt-12" :statistics="value.statistics" />
		<ProfileInsights class="mt-12" :highlights="value.statistics.highlights" />

		<section class="mt-12" aria-labelledby="recent-heading">
			<div>
				<h2 id="recent-heading" class="text-lg font-semibold text-highlighted">
					Recently heard
				</h2>
				<p class="mt-1 text-xs text-muted">
					The latest tracks this listener actually heard.
				</p>
			</div>
			<ul v-if="recentTracks.length" class="mt-5 grid gap-2 lg:grid-cols-2">
				<li
					v-for="track in recentTracks"
					:key="track.playback_id"
					class="flex min-w-0 items-center gap-3 rounded-xl bg-elevated/35 p-2.5"
				>
					<SharedTrackIdentity
						:entry="track"
						:title="track.title"
						:artist-names="track.artist_names"
						class="min-w-0 flex-1"
						multiline
					/>
					<div class="shrink-0 text-right text-xs text-muted">
						<p class="tabular-nums">
							{{ formatStatisticsDuration(track.audio_seconds) }}
						</p>
						<p class="mt-0.5">{{ formatDate(track.last_heard_at) }}</p>
					</div>
				</li>
			</ul>
			<p v-else class="mt-5 text-sm text-muted">No recent listening yet.</p>
		</section>
	</div>
</template>
