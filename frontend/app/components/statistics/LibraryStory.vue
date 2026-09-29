<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { GroupStatistics } from "~/core/models/statistics";
import { formatStatistic } from "~/core/models/statistics";

type LibraryStatistics = GroupStatistics["library"];
type RankedTrack = LibraryStatistics["top_liked_tracks"][number];

const props = withDefaults(defineProps<{ library?: LibraryStatistics; loading?: boolean }>(), {
	loading: false,
});
const { icons } = useTheme();
const metrics = computed(() => [
	{
		label: "Likes changed",
		value: formatStatistic(props.library?.likes ?? 0),
		icon: icons.value.like,
	},
	{
		label: "Dislikes changed",
		value: formatStatistic(props.library?.dislikes ?? 0),
		icon: icons.value.dislike,
	},
	{
		label: "Public playlists created",
		value: formatStatistic(props.library?.public_playlists ?? 0),
		icon: icons.value.folderOpen,
	},
	{
		label: "Shared playlists created",
		value: formatStatistic(props.library?.shared_playlists ?? 0),
		icon: icons.value.users,
	},
]);
const likeShare = computed(() => Math.round((props.library?.like_share ?? 0) * 100));
const dislikeShare = computed(() => (props.library?.reactions ? 100 - likeShare.value : 0));
const rankings = computed<
	readonly { key: string; title: string; description: string; tracks: readonly RankedTrack[] }[]
>(() => [
	{
		key: "liked",
		title: "Most liked",
		description: "Current likes set during this period.",
		tracks: props.library?.top_liked_tracks ?? [],
	},
	{
		key: "disliked",
		title: "Most disliked",
		description: "Current dislikes set during this period.",
		tracks: props.library?.top_disliked_tracks ?? [],
	},
	{
		key: "saved",
		title: "Most saved together",
		description: "Adds to public or collaborative playlists during this period.",
		tracks: props.library?.most_saved_tracks ?? [],
	},
]);
</script>

<template>
	<div class="space-y-8">
		<StatisticsMetricGrid :items="metrics" :loading="loading" />

		<section aria-labelledby="reaction-balance-heading">
			<div class="flex items-end justify-between gap-4">
				<div>
					<h2
						id="reaction-balance-heading"
						class="text-lg font-semibold text-highlighted"
					>
						Reaction balance
					</h2>
					<p class="mt-1 text-xs text-muted">
						Current reactions that were created or changed in this period.
					</p>
				</div>
				<USkeleton v-if="loading" class="h-4 w-20" />
				<p v-else class="text-sm font-medium tabular-nums text-highlighted">
					{{ library?.reactions ?? 0 }} reactions
				</p>
			</div>
			<div v-if="loading" class="mt-4 flex h-2 gap-1 overflow-hidden rounded-full">
				<USkeleton class="h-full w-2/3 rounded-full" />
				<USkeleton class="h-full flex-1 rounded-full" />
			</div>
			<div v-else class="mt-4">
				<div class="flex h-2 overflow-hidden rounded-full bg-default">
					<div
						class="h-full bg-primary transition-[width]"
						:style="{ width: `${likeShare}%` }"
					/>
				</div>
				<div class="mt-2 flex justify-between gap-4 text-xs text-muted">
					<span>{{ likeShare }}% likes</span>
					<span>{{ dislikeShare }}% dislikes</span>
				</div>
			</div>
		</section>

		<div class="grid gap-8 xl:grid-cols-3">
			<section v-for="ranking in rankings" :key="ranking.key" class="min-w-0">
				<h2 class="text-sm font-semibold text-highlighted">{{ ranking.title }}</h2>
				<p class="mt-1 text-xs text-muted">{{ ranking.description }}</p>
				<ol v-if="loading" class="mt-4 space-y-1" aria-hidden="true">
					<li v-for="index in 5" :key="index" class="flex items-center gap-3 px-2 py-1.5">
						<SharedTrackIdentity loading size="sm" class="min-w-0 flex-1" />
						<USkeleton class="h-3 w-8 shrink-0" />
					</li>
				</ol>
				<ol v-else-if="ranking.tracks.length" class="mt-4 space-y-1">
					<li
						v-for="(track, index) in ranking.tracks"
						:key="track.track_id"
						class="flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-elevated/50"
					>
						<span class="w-4 shrink-0 text-right text-xs tabular-nums text-dimmed">
							{{ index + 1 }}
						</span>
						<SharedTrackIdentity
							:entry="{ artwork_url: track.artwork_url }"
							:title="track.title"
							:artist-names="track.artist_names"
							size="sm"
							class="min-w-0 flex-1"
							multiline
						/>
						<span class="shrink-0 text-xs tabular-nums text-muted"
							>{{ track.count }}×</span
						>
					</li>
				</ol>
				<p v-else class="mt-4 text-sm text-muted">Nothing recorded in this period.</p>
			</section>
		</div>
	</div>
</template>
