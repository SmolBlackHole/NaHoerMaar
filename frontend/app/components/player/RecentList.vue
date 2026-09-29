<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import { formatTime } from "~/core/models/player";
import type { PlaybackHistoryEntry } from "~/core/models/playbacks";

const props = withDefaults(
	defineProps<{
		entries: PlaybackHistoryEntry[];
		loading?: boolean;
		skeletonCount?: number;
		layout?: "compact" | "history";
		selectable?: boolean;
		selectedPlaybackIds?: Set<string>;
	}>(),
	{
		loading: false,
		skeletonCount: 5,
		layout: "compact",
		selectable: false,
		selectedPlaybackIds: () => new Set(),
	},
);
const emit = defineEmits<{ toggle: [entry: PlaybackHistoryEntry] }>();
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const session = useNuxtApp().$backendCore.stores.useSessionStore();
const reactions = useNuxtApp().$backendCore.stores.useLibraryStore();
const { icons } = useTheme();
const listLabel = computed(() =>
	props.layout === "history" ? "Playback history" : "Recently played tracks",
);
function playedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}
function artistUrl(item: PlaybackHistoryEntry) {
	const artist = item.artist_names.join(", ");
	return artist ? `https://music.youtube.com/search?q=${encodeURIComponent(artist)}` : null;
}
function endState(item: PlaybackHistoryEntry) {
	if (!item.ended_at) return "Playing now";
	switch (item.end_reason) {
		case "completed":
			return "Completed";
		case "skipped":
			return "Skipped";
		case "stopped":
			return "Stopped";
		case "failed":
			return "Failed";
		default:
			return "Ended";
	}
}
function requeue(item: PlaybackHistoryEntry) {
	return player.add([{ track_id: item.track_id, source_id: null }]);
}
watch(
	() => [props.entries.map(({ track_id }) => track_id).join(","), session.status] as const,
	([, status]) => {
		if (status === "authenticated")
			void reactions.load(props.entries.map(({ track_id }) => track_id));
	},
	{ immediate: true },
);
</script>

<template>
	<div>
		<div
			v-if="props.layout === 'history'"
			class="recent-columns"
			:class="{ 'recent-columns--selectable': selectable }"
			aria-hidden="true"
		>
			<span v-if="selectable"></span>
			<span class="recent-column-track">Track</span>
			<span>Requested by</span>
			<span>Length</span>
			<span>Played</span>
			<span></span>
		</div>
		<ol
			v-if="loading"
			:aria-label="`Loading ${listLabel.toLowerCase()}`"
			class="recent-list space-y-1"
			:class="{ 'recent-list--history': props.layout === 'history' }"
		>
			<li
				v-for="index in props.skeletonCount"
				:key="index"
				class="recent-row flex flex-wrap items-center gap-3 py-4"
				aria-hidden="true"
			>
				<USkeleton v-if="selectable" class="recent-select size-4" />
				<SharedTrackIdentity loading class="recent-track" />
				<div class="recent-contributor flex min-w-0 items-center gap-2">
					<USkeleton class="size-6 shrink-0 rounded-full" />
					<USkeleton class="h-3 w-20" />
				</div>
				<USkeleton class="recent-duration h-3 w-10" />
				<div class="recent-moment hidden w-36 space-y-2 lg:block">
					<USkeleton class="h-3 w-28" />
					<USkeleton class="h-3 w-16" />
				</div>
				<div class="recent-actions ml-auto flex items-center gap-1">
					<USkeleton class="size-10 rounded-lg" />
					<USkeleton class="size-10 rounded-lg" />
					<USkeleton class="size-11 rounded-lg" />
					<USkeleton class="size-11 rounded-lg" />
				</div>
			</li>
		</ol>
		<ol
			v-else-if="entries.length"
			:aria-label="listLabel"
			class="recent-list space-y-1"
			:class="{
				'recent-list--history': props.layout === 'history',
				'recent-list--selectable': selectable,
			}"
		>
			<li
				v-for="item in entries"
				:key="item.playback_id"
				class="recent-row flex flex-wrap items-center gap-3 py-4"
			>
				<label v-if="selectable" class="recent-select grid size-10 place-items-center">
					<input
						type="checkbox"
						:checked="selectedPlaybackIds.has(item.playback_id)"
						@change="emit('toggle', item)"
					/>
					<span class="sr-only">Select {{ item.title }}</span>
				</label>
				<SharedTrackIdentity
					:entry="{ artwork_url: item.artwork_url }"
					:title="item.title"
					:artist-names="item.artist_names"
					:source-url="item.source_url"
					:artist-url="artistUrl(item)"
					class="recent-track"
					multiline
				>
					<template #metadata>
						<span class="recent-mobile-duration shrink-0 tabular-nums">
							{{ formatTime(item.duration_seconds) }}
						</span>
						<span class="recent-mobile-state shrink-0">{{ endState(item) }}</span>
					</template>
				</SharedTrackIdentity>
				<div v-if="item.contributor" class="recent-contributor min-w-0">
					<PlayerContributor
						:contributor="item.contributor"
						:origin="item.origin"
						class="w-full"
					/>
				</div>
				<span class="recent-duration text-xs tabular-nums text-muted">
					{{ formatTime(item.duration_seconds) }}
				</span>
				<div class="recent-moment hidden w-36 text-xs text-muted lg:block">
					<time :datetime="item.started_at" class="block tabular-nums">
						{{ playedAt(item.started_at) }}
					</time>
					<span class="mt-1 block">{{ endState(item) }}</span>
				</div>
				<div class="recent-actions ml-auto flex items-center">
					<LibraryReactionActions
						:track-id="item.track_id"
						:title="item.title"
						compact
						:show-counts="false"
						:show-details="false"
						mode="menu"
					/>
					<LibraryPlaylistAction
						:track="{ track_id: item.track_id, preferred_source_id: item.source_id }"
						:title="item.title"
					/>
					<UTooltip :text="`Queue ${item.title} again`">
						<UButton
							:icon="icons.plus"
							color="neutral"
							variant="ghost"
							:aria-label="`Queue ${item.title} again`"
							class="recent-add ml-auto size-11 shrink-0 justify-center"
							:disabled="!player.canControl"
							:loading="player.isPending('queue.add')"
							@click="requeue(item)"
						/>
					</UTooltip>
					<PlayerRadioAction
						v-if="item.source_id"
						:source-id="item.source_id"
						:title="item.title"
					/>
				</div>
			</li>
		</ol>
		<p v-else class="py-6 text-sm text-muted">Tracks appear here once they start playing.</p>
	</div>
</template>

<style scoped>
.recent-columns {
	display: grid;
	grid-template-columns: minmax(14rem, 1fr) minmax(8rem, 12rem) 3rem 9rem 10.5rem;
	gap: 1rem;
	align-items: center;
	padding: 0 0.75rem 0.75rem;
	font-size: 0.6875rem;
	font-weight: 500;
	letter-spacing: 0.04em;
	text-transform: uppercase;
	color: var(--ui-text-dimmed);
}
.recent-column-track {
	grid-column: 1;
}
.recent-columns--selectable {
	grid-template-columns: 2.5rem minmax(14rem, 1fr) minmax(8rem, 12rem) 3rem 9rem 10.5rem;
}
.recent-columns--selectable .recent-column-track {
	grid-column: 2;
}
.recent-row {
	padding-inline-start: 0.75rem;
	border-radius: 0.5rem;
	transition: background-color 140ms ease-out;
}
.recent-list--history .recent-row {
	display: grid;
	grid-template-columns: minmax(14rem, 1fr) minmax(8rem, 12rem) 3rem 9rem 10.5rem;
	gap: 1rem;
}
.recent-list--history.recent-list--selectable .recent-row {
	grid-template-columns: 2.5rem minmax(14rem, 1fr) minmax(8rem, 12rem) 3rem 9rem 10.5rem;
}
.recent-select input {
	width: 1rem;
	height: 1rem;
	accent-color: var(--ui-primary);
}
.recent-list--history .recent-track {
	flex: none;
}
.recent-list--history .recent-actions {
	margin-left: 0;
	justify-content: flex-end;
}
.recent-row:hover,
.recent-row:focus-within {
	background: var(--ui-bg-muted);
}
.recent-mobile-duration {
	display: none;
}
.recent-mobile-state {
	display: none;
}
@container workspace (max-width: 600px) {
	.recent-columns {
		display: none;
	}
	.recent-list--history .recent-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 0.75rem;
	}
	.recent-list--history.recent-list--selectable .recent-row {
		grid-template-columns: 2rem minmax(0, 1fr) auto;
	}
	.recent-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 0.75rem;
		align-items: start;
		padding-block: 1rem;
	}
	.recent-track {
		--track-artwork-size: 2.75rem;
	}
	.recent-select {
		grid-column: 1;
		grid-row: 1;
		align-self: start;
	}
	.recent-list--selectable .recent-track {
		grid-column: 2;
	}
	.recent-list--selectable .recent-contributor {
		grid-column: 2;
	}
	.recent-list--selectable .recent-actions {
		grid-column: 3;
	}
	.recent-title {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		white-space: normal;
		overflow-wrap: anywhere;
	}
	.recent-actions {
		grid-column: 2;
		grid-row: 2;
		align-self: center;
	}
	.recent-track {
		grid-column: 1;
	}
	.recent-contributor {
		grid-column: 1;
		grid-row: 2;
		overflow: hidden;
	}
	.recent-mobile-duration {
		display: inline;
	}
	.recent-mobile-state {
		display: inline;
	}
	.recent-duration,
	.recent-moment {
		display: none;
	}
}
@container workspace (min-width: 601px) and (max-width: 950px) {
	.recent-columns {
		display: none;
	}
	.recent-list--history .recent-row {
		display: flex;
		gap: 0.75rem;
	}
	.recent-list--history.recent-list--selectable .recent-row {
		display: grid;
		grid-template-columns: 2.5rem minmax(8rem, 1fr) minmax(8rem, 12rem) auto;
	}
	.recent-list--history .recent-track {
		flex: 1 1 8rem;
	}
	.recent-list--history .recent-actions {
		margin-left: auto;
	}
}
</style>
