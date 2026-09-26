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
const metrics = computed(() => {
	const totals = props.value.statistics.totals;
	return [
		{
			label: "Time heard",
			value: formatStatisticsDuration(totals.listening_seconds),
			icon: icons.value.headphones,
		},
		{
			label: "Channel presence",
			value: formatStatisticsDuration(totals.presence_seconds),
			icon: icons.value.users,
		},
		{
			label: "Requests",
			value: formatStatistic(totals.requests.total),
			icon: icons.value.radio,
		},
		{
			label: "Confirmed plays",
			value: formatStatistic(totals.playback.overall.started),
			icon: icons.value.play,
		},
	];
});
const secondaryMetrics = computed(() => {
	const totals = props.value.statistics.totals;
	return [
		{
			label: "Completed",
			value: formatStatistic(totals.playback.overall.completed),
			icon: icons.value.check,
		},
		{
			label: "Skipped",
			value: formatStatistic(totals.playback.overall.skipped),
			icon: icons.value.skip,
		},
		{
			label: "Different tracks",
			value: formatStatistic(totals.unique_tracks),
			icon: icons.value.music,
		},
		{
			label: "Different artists",
			value: formatStatistic(totals.unique_artists),
			icon: icons.value.user,
		},
	];
});
const topTracks = computed(() => props.value.statistics.top_tracks.slice(0, 5));
const topArtists = computed(() => props.value.statistics.top_artists.slice(0, 5));
const recentTracks = computed(() => props.value.recent_tracks.slice(0, 6));

function formatDate(value: string) {
	return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
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
			Statistics start on
			{{
				value.statistics.coverage.recorded_since
					? formatDate(value.statistics.coverage.recorded_since)
					: "the first recorded activity"
			}}. Earlier activity is not included.
		</p>

		<slot name="details" />

		<div class="mt-10 grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(18rem,1fr)]">
			<StatisticsActivity :activity="value.statistics.activity" />
			<section aria-labelledby="profile-request-heading">
				<h2 id="profile-request-heading" class="text-lg font-semibold text-highlighted">
					Requests and outcomes
				</h2>
				<dl class="mt-4 space-y-1 rounded-xl bg-elevated/40 p-2">
					<div class="flex justify-between gap-4 rounded-lg px-3 py-2.5">
						<dt class="text-sm text-muted">Manual requests</dt>
						<dd class="font-medium tabular-nums">
							{{ value.statistics.totals.requests.manual }}
						</dd>
					</div>
					<div class="flex justify-between gap-4 rounded-lg px-3 py-2.5">
						<dt class="text-sm text-muted">Radio requests</dt>
						<dd class="font-medium tabular-nums">
							{{ value.statistics.totals.requests.radio }}
						</dd>
					</div>
					<div class="flex justify-between gap-4 rounded-lg px-3 py-2.5">
						<dt class="text-sm text-muted">Stopped</dt>
						<dd class="font-medium tabular-nums">
							{{ value.statistics.totals.playback.overall.stopped }}
						</dd>
					</div>
					<div class="flex justify-between gap-4 rounded-lg px-3 py-2.5">
						<dt class="text-sm text-muted">Failed</dt>
						<dd class="font-medium tabular-nums">
							{{ value.statistics.totals.playback.overall.failed }}
						</dd>
					</div>
				</dl>
			</section>
		</div>

		<StatisticsMetricGrid class="mt-8" :items="secondaryMetrics" />

		<div class="mt-10 grid min-w-0 gap-10 xl:grid-cols-3">
			<section class="min-w-0" aria-labelledby="top-tracks-heading">
				<h2 id="top-tracks-heading" class="text-lg font-semibold text-highlighted">
					Most played tracks
				</h2>
				<ol v-if="topTracks.length" class="mt-5 space-y-2">
					<li
						v-for="track in topTracks"
						:key="track.track_id"
						class="flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-elevated/50"
					>
						<PlayerTrackArtwork
							:entry="{ artwork_url: track.artwork_url }"
							class="size-10 shrink-0"
						/>
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium text-highlighted">
								{{ track.title }}
							</p>
							<p class="mt-0.5 truncate text-xs text-muted">
								{{ track.artist_names.join(", ") || "Unknown artist" }}
							</p>
						</div>
						<span class="shrink-0 text-xs tabular-nums text-muted">
							{{ track.plays }} plays
						</span>
					</li>
				</ol>
				<p v-else class="mt-5 text-sm text-muted">No confirmed tracks in this period.</p>
			</section>

			<section class="min-w-0" aria-labelledby="top-artists-heading">
				<h2 id="top-artists-heading" class="text-lg font-semibold text-highlighted">
					Most played artists
				</h2>
				<ol v-if="topArtists.length" class="mt-5 space-y-2">
					<li
						v-for="(artist, index) in topArtists"
						:key="artist.artist_id"
						class="flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-elevated/50"
					>
						<span class="w-5 shrink-0 text-right text-sm tabular-nums text-muted">{{
							index + 1
						}}</span>
						<p class="min-w-0 flex-1 truncate text-sm font-medium text-highlighted">
							{{ artist.name }}
						</p>
						<span class="shrink-0 text-xs tabular-nums text-muted"
							>{{ artist.plays }} plays</span
						>
					</li>
				</ol>
				<p v-else class="mt-5 text-sm text-muted">No artists in this period.</p>
			</section>

			<section class="min-w-0" aria-labelledby="recent-heading">
				<h2 id="recent-heading" class="text-lg font-semibold text-highlighted">
					Recently heard
				</h2>
				<ul v-if="recentTracks.length" class="mt-5 space-y-2">
					<li
						v-for="track in recentTracks"
						:key="track.playback_id"
						class="flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-elevated/50"
					>
						<img
							v-if="track.artwork_url"
							:src="track.artwork_url"
							alt=""
							width="40"
							height="40"
							class="size-10 shrink-0 rounded-lg object-cover"
							loading="lazy"
						/>
						<div
							v-else
							class="grid size-10 shrink-0 place-items-center rounded-lg bg-elevated"
						>
							<UIcon :name="icons.music" class="size-4 text-muted" />
						</div>
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium text-highlighted">
								{{ track.title }}
							</p>
							<p class="mt-0.5 truncate text-xs text-muted">
								{{ track.artist_names.join(", ") || "Unknown artist" }}
							</p>
						</div>
						<span class="shrink-0 text-xs text-muted">{{
							formatDate(track.last_heard_at)
						}}</span>
					</li>
				</ul>
				<p v-else class="mt-5 text-sm text-muted">No recent listening in this period.</p>
			</section>
		</div>
	</div>
</template>
