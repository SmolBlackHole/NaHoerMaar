<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { GroupStatistics } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

const props = defineProps<{ report: GroupStatistics }>();
const consent = useConsentStore();
const { icons } = useTheme();
const featured = computed(() => props.report.top_tracks[0]);
const remainingTracks = computed(() => props.report.top_tracks.slice(1));
const artworkFailed = ref(false);
const featuredArtwork = computed(() =>
	consent.youtube ? (featured.value?.artwork_url ?? null) : null,
);

watch(featuredArtwork, () => {
	artworkFailed.value = false;
});
</script>

<template>
	<section aria-labelledby="music-heading">
		<div>
			<h2 id="music-heading" class="text-lg font-semibold text-highlighted">The music</h2>
			<p class="mt-1 text-xs text-muted">The tracks and artists that kept coming back.</p>
		</div>

		<div
			v-if="featured"
			class="mt-4 grid gap-8 xl:grid-cols-[minmax(17rem,30rem)_minmax(0,1fr)]"
		>
			<article class="featured-track">
				<img
					v-if="featuredArtwork && !artworkFailed"
					:src="featuredArtwork"
					alt=""
					referrerpolicy="no-referrer"
					class="featured-track-artwork"
					@error="artworkFailed = true"
				/>
				<div v-else class="featured-track-placeholder" aria-hidden="true">
					<UIcon :name="icons.music" />
				</div>
				<div class="featured-track-blur" aria-hidden="true" />
				<div class="featured-track-shade" aria-hidden="true" />

				<div class="featured-track-copy">
					<div class="flex items-center justify-between gap-3">
						<span class="featured-track-label">
							<UIcon :name="icons.play" class="size-3.5" />
							Most played
						</span>
						<span class="featured-track-count">
							{{ featured.plays }} {{ featured.plays === 1 ? "play" : "plays" }}
						</span>
					</div>

					<div class="mt-auto pt-12">
						<h3 class="line-clamp-2 text-2xl font-semibold tracking-tight text-white">
							{{ featured.title }}
						</h3>
						<p class="mt-2 truncate text-sm text-white/75">
							{{ featured.artist_names.join(", ") || "Unknown artist" }}
						</p>
						<p class="mt-4 text-xs tabular-nums text-white/60">
							{{ formatStatisticsDuration(featured.listening_seconds) }} heard
							together
						</p>
					</div>
				</div>
			</article>

			<div class="grid min-w-0 gap-8 md:grid-cols-2">
				<section class="min-w-0" aria-labelledby="tracks-heading">
					<h3 id="tracks-heading" class="text-sm font-semibold text-highlighted">
						More on repeat
					</h3>
					<ol v-if="remainingTracks.length" class="mt-3 space-y-1">
						<li
							v-for="track in remainingTracks"
							:key="track.track_id"
							class="flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-elevated/50"
						>
							<SharedTrackIdentity
								:entry="{ artwork_url: track.artwork_url }"
								:title="track.title"
								:artist-names="track.artist_names"
								size="sm"
								class="min-w-0 flex-1"
								multiline
							/>
							<span class="shrink-0 text-xs tabular-nums text-muted"
								>{{ track.plays }}×</span
							>
						</li>
					</ol>
					<p v-else class="mt-3 text-sm text-muted">No other confirmed tracks yet.</p>
				</section>

				<section class="min-w-0" aria-labelledby="artists-heading">
					<h3 id="artists-heading" class="text-sm font-semibold text-highlighted">
						Artists on repeat
					</h3>
					<ol v-if="report.top_artists.length" class="mt-3 space-y-1">
						<li
							v-for="(artist, index) in report.top_artists"
							:key="artist.artist_id"
							class="flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-elevated/50"
						>
							<span class="w-5 shrink-0 text-right text-xs tabular-nums text-muted">{{
								index + 1
							}}</span>
							<p class="min-w-0 flex-1 truncate text-sm font-medium text-highlighted">
								{{ artist.name }}
							</p>
							<span class="shrink-0 text-xs tabular-nums text-muted"
								>{{ artist.plays }}×</span
							>
						</li>
					</ol>
					<p v-else class="mt-3 text-sm text-muted">No artists in this period.</p>
				</section>
			</div>
		</div>
		<p v-else class="mt-4 text-sm text-muted">No confirmed tracks in this period.</p>
	</section>
</template>

<style scoped>
.featured-track {
	position: relative;
	isolation: isolate;
	width: 100%;
	min-width: 0;
	aspect-ratio: 16 / 9;
	min-height: 14rem;
	overflow: hidden;
	border-radius: 1rem;
	background: #111318;
}
.featured-track-artwork,
.featured-track-placeholder,
.featured-track-blur,
.featured-track-shade {
	position: absolute;
	inset: 0;
}
.featured-track-artwork {
	width: 100%;
	height: 100%;
	object-fit: cover;
	transition: transform 700ms cubic-bezier(0.16, 1, 0.3, 1);
}
.featured-track:hover .featured-track-artwork {
	transform: scale(1.035);
}
.featured-track-placeholder {
	display: grid;
	place-items: center;
	background:
		radial-gradient(
			circle at 72% 24%,
			color-mix(in srgb, var(--ui-primary) 28%, transparent),
			transparent 38%
		),
		#111318;
	color: rgb(255 255 255 / 18%);
}
.featured-track-placeholder > span {
	width: 7rem;
	height: 7rem;
}
.featured-track-blur {
	backdrop-filter: blur(18px);
	mask-image: linear-gradient(42deg, #000 0%, #000 16%, transparent 68%);
}
.featured-track-shade {
	background:
		linear-gradient(0deg, rgb(5 7 10 / 94%), rgb(5 7 10 / 34%) 52%, rgb(5 7 10 / 12%)),
		linear-gradient(90deg, rgb(5 7 10 / 58%), transparent 74%);
}
.featured-track-copy {
	position: relative;
	z-index: 1;
	display: flex;
	height: 100%;
	min-height: 14rem;
	flex-direction: column;
	padding: 1.25rem;
}
.featured-track-label,
.featured-track-count {
	display: inline-flex;
	align-items: center;
	gap: 0.4rem;
	border-radius: 999px;
	background: rgb(8 10 13 / 58%);
	padding: 0.4rem 0.65rem;
	font-size: 0.75rem;
	font-weight: 600;
	color: rgb(255 255 255 / 82%);
	backdrop-filter: blur(12px);
}
.featured-track-count {
	font-variant-numeric: tabular-nums;
}
@media (max-width: 639px) {
	.featured-track {
		aspect-ratio: 4 / 3;
	}
	.featured-track-copy {
		min-height: 0;
		padding: 1rem;
	}
}
@media (prefers-reduced-motion: reduce) {
	.featured-track-artwork {
		transition: none;
	}
}
</style>
