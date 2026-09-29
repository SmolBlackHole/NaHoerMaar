<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { PersonalStatistics } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

const props = defineProps<{ statistics: PersonalStatistics }>();
const ranking = ref<"plays" | "listening">("plays");
const tracks = computed(() =>
	ranking.value === "plays"
		? props.statistics.top_tracks
		: props.statistics.top_tracks_by_listening,
);
const artists = computed(() =>
	ranking.value === "plays"
		? props.statistics.top_artists
		: props.statistics.top_artists_by_listening,
);

function value(plays: number, listeningSeconds: number) {
	return ranking.value === "plays"
		? `${plays} ${plays === 1 ? "play" : "plays"}`
		: `${formatStatisticsDuration(listeningSeconds)} heard`;
}
</script>

<template>
	<section aria-labelledby="profile-favourites-heading">
		<div class="flex flex-wrap items-end justify-between gap-4">
			<div>
				<h2 id="profile-favourites-heading" class="text-lg font-semibold text-highlighted">
					Listening favourites
				</h2>
				<p class="mt-1 text-xs text-muted">
					Derived from repeat plays and time heard, not explicit Likes.
				</p>
			</div>
			<div class="flex gap-1" role="group" aria-label="Listening favourites ranking">
				<UButton
					label="Most played"
					:color="ranking === 'plays' ? 'primary' : 'neutral'"
					:variant="ranking === 'plays' ? 'soft' : 'ghost'"
					size="xs"
					:aria-pressed="ranking === 'plays'"
					@click="ranking = 'plays'"
				/>
				<UButton
					label="Most heard"
					:color="ranking === 'listening' ? 'primary' : 'neutral'"
					:variant="ranking === 'listening' ? 'soft' : 'ghost'"
					size="xs"
					:aria-pressed="ranking === 'listening'"
					@click="ranking = 'listening'"
				/>
			</div>
		</div>

		<div class="mt-5 grid gap-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(16rem,0.8fr)]">
			<ol v-if="tracks.length" class="grid gap-2 sm:grid-cols-2">
				<li
					v-for="(track, index) in tracks.slice(0, 6)"
					:key="track.track_id"
					class="group flex min-w-0 items-center gap-3 rounded-xl bg-elevated/35 p-2.5 transition-colors hover:bg-elevated/65"
				>
					<SharedTrackIdentity
						:entry="track"
						:title="track.title"
						:artist-names="track.artist_names"
						:position="index + 1"
						class="min-w-0 flex-1"
						multiline
					/>
					<span class="shrink-0 text-xs tabular-nums text-muted">
						{{ value(track.plays, track.listening_seconds) }}
					</span>
				</li>
			</ol>
			<p v-else class="text-sm text-muted">No listening favourites in this period yet.</p>

			<ol v-if="artists.length" class="space-y-2">
				<li
					v-for="(artist, index) in artists.slice(0, 5)"
					:key="artist.artist_id"
					class="grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-2 py-2"
				>
					<span class="text-right text-xs font-semibold tabular-nums text-dimmed">
						{{ index + 1 }}
					</span>
					<p class="truncate text-sm font-medium text-highlighted">{{ artist.name }}</p>
					<span class="text-xs tabular-nums text-muted">
						{{ value(artist.plays, artist.listening_seconds) }}
					</span>
				</li>
			</ol>
		</div>
	</section>
</template>
