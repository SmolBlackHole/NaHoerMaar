<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { PersonalStatistics } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

const props = defineProps<{ highlights: PersonalStatistics["highlights"] }>();
const { icons } = useTheme();
const requestOutcomes = computed(() => props.highlights.request_outcomes);

function percent(value: number | null) {
	return value === null ? "Not enough data" : `${Math.round(value * 100)}%`;
}
</script>

<template>
	<section aria-labelledby="profile-insights-heading">
		<div>
			<h2 id="profile-insights-heading" class="text-lg font-semibold text-highlighted">
				Listening stories
			</h2>
			<p class="mt-1 text-xs text-muted">
				Requests that landed, Radio finds and music shared onward.
			</p>
		</div>

		<div class="mt-5 grid gap-3 xl:grid-cols-3">
			<article class="rounded-2xl bg-elevated/40 p-5">
				<div class="flex items-center gap-2 text-sm font-medium text-highlighted">
					<UIcon :name="icons.check" class="size-4 text-primary" />
					Request journey
				</div>
				<div class="mt-5 space-y-4">
					<div>
						<div class="flex items-baseline justify-between gap-3">
							<span class="text-xs text-muted">Reached playback</span>
							<strong class="text-sm tabular-nums">{{
								percent(requestOutcomes.play_rate)
							}}</strong>
						</div>
						<div class="mt-2 h-1.5 overflow-hidden rounded-full bg-default">
							<div
								class="h-full rounded-full bg-primary"
								:style="{ width: `${(requestOutcomes.play_rate ?? 0) * 100}%` }"
							/>
						</div>
					</div>
					<div>
						<div class="flex items-baseline justify-between gap-3">
							<span class="text-xs text-muted">Played to completion</span>
							<strong class="text-sm tabular-nums">{{
								percent(requestOutcomes.completion_rate)
							}}</strong>
						</div>
						<div class="mt-2 h-1.5 overflow-hidden rounded-full bg-default">
							<div
								class="h-full rounded-full bg-primary/70"
								:style="{
									width: `${(requestOutcomes.completion_rate ?? 0) * 100}%`,
								}"
							/>
						</div>
					</div>
					<p class="text-xs text-muted">
						{{ requestOutcomes.played_requests }} of
						{{ requestOutcomes.manual_requests }} manual requests played.
					</p>
				</div>
			</article>

			<article class="rounded-2xl bg-elevated/40 p-5">
				<div class="flex items-center gap-2 text-sm font-medium text-highlighted">
					<UIcon :name="icons.radio" class="size-4 text-primary" />
					Radio discoveries
				</div>
				<ul v-if="highlights.radio_discoveries.length" class="mt-4 space-y-3">
					<li
						v-for="track in highlights.radio_discoveries"
						:key="track.track_id"
						class="flex min-w-0 items-center gap-3"
					>
						<PlayerTrackArtwork :entry="track" class="size-10 shrink-0 rounded-lg" />
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium text-highlighted">
								{{ track.title }}
							</p>
							<p class="mt-0.5 truncate text-xs text-muted">
								{{ track.artist_names.join(", ") || "Unknown artist" }}
							</p>
						</div>
						<span class="text-xs tabular-nums text-muted">
							{{ formatStatisticsDuration(track.listening_seconds) }}
						</span>
					</li>
				</ul>
				<p v-else class="mt-4 text-sm text-muted">No Radio discoveries in this period.</p>
			</article>

			<article class="rounded-2xl bg-elevated/40 p-5">
				<div class="flex items-center gap-2 text-sm font-medium text-highlighted">
					<UIcon :name="icons.users" class="size-4 text-primary" />
					Picked up by others
				</div>
				<ul v-if="highlights.influenced_tracks.length" class="mt-4 space-y-3">
					<li
						v-for="track in highlights.influenced_tracks"
						:key="track.track_id"
						class="flex min-w-0 items-center gap-3"
					>
						<PlayerTrackArtwork :entry="track" class="size-10 shrink-0 rounded-lg" />
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium text-highlighted">
								{{ track.title }}
							</p>
							<p class="mt-0.5 truncate text-xs text-muted">
								{{ track.artist_names.join(", ") || "Unknown artist" }}
							</p>
						</div>
						<span class="text-right text-xs text-muted">
							{{ track.distinct_listeners }}
							{{ track.distinct_listeners === 1 ? "listener" : "listeners" }}
						</span>
					</li>
				</ul>
				<p v-else class="mt-4 text-sm text-muted">
					Nothing has spread from this queue yet.
				</p>
			</article>
		</div>
	</section>
</template>
