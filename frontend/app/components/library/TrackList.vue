<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ContextMenuItem } from "@nuxt/ui";
import type { LibraryTrack } from "~/core/models/library";
import { formatDuration } from "~/core/models/catalog";
import { youtubeMusicArtistUrl, youtubeMusicTrackUrl } from "~/core/models/musicLinks";

const props = withDefaults(
	defineProps<{
		items: LibraryTrack[];
		loading?: boolean;
		skeletonCount?: number;
		startIndex?: number;
		reactionControls?: boolean;
		savedLabel?: string;
	}>(),
	{
		loading: false,
		skeletonCount: 8,
		startIndex: 0,
		reactionControls: true,
		savedLabel: "Saved",
	},
);
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const reactions = useNuxtApp().$backendCore.stores.useLibraryStore();
const { icons } = useTheme();

function reactedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		year: "numeric",
	}).format(new Date(value));
}

function queue(track: LibraryTrack) {
	return player.add([{ track_id: track.track_id, source_id: null }]);
}

function contextItems(track: LibraryTrack): ContextMenuItem[][] {
	const items: ContextMenuItem[][] = [
		[
			{
				label: "Add to queue",
				icon: icons.value.plus,
				disabled: !player.canControl,
				onSelect: () => void queue(track),
			},
		],
	];
	if (props.reactionControls) {
		items.push([
			{
				label: "Like",
				icon: icons.value.like,
				disabled: track.reaction === "like",
				onSelect: () => void reactions.setReaction(track.track_id, "like"),
			},
			{
				label: "Dislike",
				icon: icons.value.dislike,
				disabled: track.reaction === "dislike",
				onSelect: () => void reactions.setReaction(track.track_id, "dislike"),
			},
			{
				label: "Remove reaction",
				icon: icons.value.close,
				onSelect: () => void reactions.removeReaction(track.track_id),
			},
		]);
	}
	return items;
}
</script>

<template>
	<div>
		<div class="library-columns library-grid" aria-hidden="true">
			<span></span>
			<span>Track</span>
			<span>{{ savedLabel }}</span>
			<span>{{ reactionControls ? "Reaction" : "" }}</span>
			<span></span>
		</div>
		<ol v-if="loading" class="library-list" aria-label="Loading library tracks">
			<SharedTrackRow
				v-for="index in skeletonCount"
				:key="index"
				class="library-row library-grid"
				identity-class="library-identity"
				:position="startIndex + index"
				loading
			>
				<template #details><USkeleton class="library-saved h-3 w-20" /></template>
				<template #actions>
					<div class="library-actions flex items-center gap-1">
						<USkeleton
							v-for="action in reactionControls ? 3 : 2"
							:key="action"
							class="size-10 rounded-lg"
						/>
					</div>
				</template>
			</SharedTrackRow>
		</ol>
		<ol v-else class="library-list" aria-label="Library tracks">
			<SharedTrackRow
				v-for="(track, index) in items"
				:key="track.track_id"
				class="library-row library-grid"
				identity-class="library-identity"
				:context-items="contextItems(track)"
				:entry="track"
				:title="track.title"
				:artist-names="track.artist_names"
				:source-url="youtubeMusicTrackUrl(track.title, track.artist_names)"
				:artist-url="youtubeMusicArtistUrl(track.artist_names)"
				:metadata="
					track.duration_seconds === null ? null : formatDuration(track.duration_seconds)
				"
				:position="startIndex + index + 1"
				multiline
			>
				<template #details>
					<time
						:datetime="track.reacted_at"
						class="library-saved text-xs tabular-nums text-muted"
						>{{ reactedAt(track.reacted_at) }}</time
					>
				</template>
				<template #actions>
					<div class="library-actions flex items-center gap-1">
						<LibraryReactionActions
							v-if="reactionControls"
							:track-id="track.track_id"
							:title="track.title"
							mode="menu"
							:show-details="false"
						/>
						<LibraryPlaylistAction
							:track="{ track_id: track.track_id, preferred_source_id: null }"
							:title="track.title"
						/>
						<UTooltip :text="`Add ${track.title} to queue`">
							<UButton
								:icon="icons.plus"
								:aria-label="`Add ${track.title} to queue`"
								color="neutral"
								variant="ghost"
								class="size-10 justify-center"
								:disabled="!player.canControl"
								:loading="player.isPending('queue.add')"
								@click="queue(track)"
							/>
						</UTooltip>
					</div>
				</template>
			</SharedTrackRow>
		</ol>
	</div>
</template>

<style scoped>
:deep(.library-grid) {
	display: grid;
	grid-template-columns: 2.5rem minmax(12rem, 1fr) 8rem auto 2.75rem;
	align-items: center;
	gap: 1rem;
}
.library-columns {
	padding: 0 0.5rem 0.75rem;
	font-size: 0.6875rem;
	font-weight: 500;
	letter-spacing: 0.04em;
	text-transform: uppercase;
	color: var(--ui-text-dimmed);
}
:deep(.library-identity) {
	grid-column: 1 / span 2;
}
.library-actions {
	grid-column: 4 / -1;
	justify-self: end;
}
@container workspace (max-width: 760px) {
	.library-columns {
		display: none;
	}
	:deep(.library-row) {
		grid-template-columns: 2.75rem minmax(0, 1fr) 2.75rem;
		gap: 0.75rem;
		padding-inline: 0;
	}
	:deep(.library-identity) {
		grid-column: 1 / -1;
		grid-row: 1;
		--track-artwork-size: 2.75rem;
	}
	.library-saved {
		display: none;
	}
	.library-actions {
		grid-column: 1 / -1;
		grid-row: 2;
		justify-self: end;
	}
}
</style>
