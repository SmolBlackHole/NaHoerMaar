<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { UserProfile } from "~/core/models/account";

const props = defineProps<{ value: UserProfile }>();
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
const discordAvatar = computed(() => {
	const discord = props.value.discord;
	if (discord.avatar_url) return discord.avatar_url;
	if (!discord.avatar_hash) return undefined;
	const extension = discord.avatar_hash.startsWith("a_") ? "gif" : "png";
	return `https://cdn.discordapp.com/avatars/${discord.id}/${discord.avatar_hash}.${extension}?size=128`;
});
const metrics = computed(() => {
	const totals = props.value.statistics.totals;
	return [
		{
			label: "Time listened",
			value: formatDuration(totals.listening_seconds),
			icon: icons.value.headphones,
		},
		{ label: "Tracks played", value: formatNumber(totals.plays), icon: icons.value.play },
		{ label: "Requests", value: formatNumber(totals.requests), icon: icons.value.radio },
		{
			label: "Different tracks",
			value: formatNumber(totals.unique_tracks),
			icon: icons.value.music,
		},
	];
});
const topTracks = computed(() => props.value.statistics.top_tracks.slice(0, 5));
const recentTracks = computed(() => props.value.recent_tracks.slice(0, 6));

function formatNumber(value: number) {
	return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
}
function formatDuration(seconds: number) {
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes} min`;
	const hours = Math.floor(minutes / 60);
	const remainder = minutes % 60;
	return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}
function formatDate(value: string) {
	return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}
</script>

<template>
	<div>
		<header
			class="flex flex-wrap items-center justify-between gap-5 border-b border-default pb-7"
		>
			<div class="flex min-w-0 items-center gap-5">
				<img
					v-if="value.profile.pixabot"
					:src="`/avatars/${value.profile.pixabot}.png`"
					alt=""
					width="80"
					height="80"
					class="size-20 shrink-0 rounded-2xl [image-rendering:pixelated]"
				/>
				<UAvatar
					v-else
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
			<p class="text-sm text-muted">Listening activity from the last 30 days</p>
		</header>

		<dl
			class="mt-7 grid overflow-hidden rounded-2xl border border-default sm:grid-cols-2 xl:grid-cols-4"
		>
			<div
				v-for="(metric, index) in metrics"
				:key="metric.label"
				class="p-5"
				:class="[
					index > 0 && 'border-t border-default sm:border-t-0',
					index % 2 === 1 && 'sm:border-l sm:border-default',
					index === 2 && 'sm:border-l-0 sm:border-t xl:border-l xl:border-t-0',
					index === 3 && 'sm:border-t xl:border-t-0',
				]"
			>
				<dt class="flex items-center gap-2 text-sm text-muted">
					<UIcon :name="metric.icon" class="size-4 shrink-0" />
					{{ metric.label }}
				</dt>
				<dd class="mt-3 text-2xl font-semibold tabular-nums text-highlighted">
					{{ metric.value }}
				</dd>
			</div>
		</dl>

		<p v-if="value.statistics.coverage.partial" class="mt-3 text-xs leading-relaxed text-muted">
			Statistics start on
			{{
				value.statistics.coverage.recorded_since
					? formatDate(value.statistics.coverage.recorded_since)
					: "the first recorded play"
			}}. Earlier listening is not included.
		</p>

		<slot name="details" />

		<div class="mt-10 grid gap-8 border-t border-default pt-8 xl:grid-cols-2">
			<section aria-labelledby="top-tracks-heading">
				<h2 id="top-tracks-heading" class="text-lg font-semibold text-highlighted">
					Most played tracks
				</h2>
				<p class="mt-1 text-sm text-muted">Ranked by plays during the last 30 days.</p>
				<ol v-if="topTracks.length" class="mt-5 divide-y divide-default">
					<li
						v-for="(track, index) in topTracks"
						:key="track.track_id"
						class="flex items-center gap-4 py-3 first:pt-0"
					>
						<span class="w-5 shrink-0 text-right text-sm tabular-nums text-muted">{{
							index + 1
						}}</span>
						<img
							v-if="track.artwork_url"
							:src="track.artwork_url"
							alt=""
							width="44"
							height="44"
							class="size-11 shrink-0 rounded-lg object-cover"
							loading="lazy"
						/>
						<div
							v-else
							class="grid size-11 shrink-0 place-items-center rounded-lg bg-elevated"
						>
							<UIcon :name="icons.music" class="size-4 text-muted" />
						</div>
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium text-highlighted">
								{{ track.title }}
							</p>
							<p class="mt-0.5 text-xs text-muted">
								{{ formatDuration(track.listening_seconds) }} listened
							</p>
						</div>
						<span class="shrink-0 text-sm tabular-nums text-muted">
							{{ track.plays }} {{ track.plays === 1 ? "play" : "plays" }}
						</span>
					</li>
				</ol>
				<p v-else class="mt-5 text-sm text-muted">
					Top tracks will appear after some listening history has been recorded.
				</p>
			</section>

			<section aria-labelledby="recent-heading">
				<h2 id="recent-heading" class="text-lg font-semibold text-highlighted">
					Recently heard
				</h2>
				<p class="mt-1 text-sm text-muted">The latest tracks this listener heard.</p>
				<ul v-if="recentTracks.length" class="mt-5 divide-y divide-default">
					<li
						v-for="track in recentTracks"
						:key="track.playback_id"
						class="flex items-center gap-4 py-3 first:pt-0"
					>
						<img
							v-if="track.artwork_url"
							:src="track.artwork_url"
							alt=""
							width="44"
							height="44"
							class="size-11 shrink-0 rounded-lg object-cover"
							loading="lazy"
						/>
						<div
							v-else
							class="grid size-11 shrink-0 place-items-center rounded-lg bg-elevated"
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
						<div class="shrink-0 text-right">
							<p class="text-xs text-muted">{{ formatDate(track.last_heard_at) }}</p>
							<p class="mt-0.5 text-xs tabular-nums text-muted">
								{{ formatDuration(track.audio_seconds) }}
							</p>
						</div>
					</li>
				</ul>
				<p v-else class="mt-5 text-sm text-muted">
					Recent listening will appear once this listener joins the music.
				</p>
			</section>
		</div>
	</div>
</template>
