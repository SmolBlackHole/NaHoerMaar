<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { LibraryTrack } from "~/core/models/library";
import { formatDuration } from "~/core/models/catalog";

const props = withDefaults(
	defineProps<{
		items: LibraryTrack[];
		loading?: boolean;
		skeletonCount?: number;
		startIndex?: number;
	}>(),
	{ loading: false, skeletonCount: 8, startIndex: 0 },
);
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
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
</script>

<template>
	<div>
		<div class="library-columns library-grid" aria-hidden="true">
			<span></span>
			<span>Track</span>
			<span>Saved</span>
			<span>Reaction</span>
			<span></span>
		</div>
		<ol v-if="loading" class="library-list" aria-label="Loading library tracks">
			<li
				v-for="index in skeletonCount"
				:key="index"
				class="library-row library-grid"
				aria-hidden="true"
			>
				<USkeleton class="mx-auto size-4" />
				<SharedTrackIdentity loading class="library-identity" />
				<USkeleton class="h-3 w-20" />
				<div class="library-actions flex items-center gap-1">
					<USkeleton class="size-10 rounded-lg" />
					<USkeleton class="size-10 rounded-lg" />
				</div>
				<USkeleton class="size-10 rounded-lg" />
			</li>
		</ol>
		<ol v-else class="library-list" aria-label="Library tracks">
			<li
				v-for="(track, index) in items"
				:key="track.track_id"
				class="library-row library-grid"
			>
				<span class="library-number text-center text-xs tabular-nums text-muted">
					{{ String(startIndex + index + 1).padStart(2, "0") }}
				</span>
				<SharedTrackIdentity
					:entry="track"
					:title="track.title"
					:artist-names="track.artist_names"
					:metadata="
						track.duration_seconds === null
							? null
							: formatDuration(track.duration_seconds)
					"
					class="library-identity"
					multiline
				/>
				<time
					:datetime="track.reacted_at"
					class="library-saved text-xs tabular-nums text-muted"
					>{{ reactedAt(track.reacted_at) }}</time
				>
				<div class="library-actions flex items-center gap-1">
					<LibraryReactionActions
						:track-id="track.track_id"
						:title="track.title"
						mode="menu"
						:show-details="false"
					/>
					<LibraryPlaylistAction
						:track="{ track_id: track.track_id, preferred_source_id: null }"
						:title="track.title"
					/>
				</div>
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
			</li>
		</ol>
	</div>
</template>

<style scoped>
.library-grid {
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
.library-row {
	min-height: 4.75rem;
	padding: 0.75rem 0.5rem;
	border-radius: 0.5rem;
	transition: background-color 140ms ease-out;
}
.library-row:hover,
.library-row:focus-within {
	background: var(--ui-bg-muted);
}
@container workspace (max-width: 760px) {
	.library-columns {
		display: none;
	}
	.library-row {
		grid-template-columns: 2rem minmax(0, 1fr) 2.75rem;
		gap: 0.75rem;
		padding-block: 1rem;
		padding-inline: 0;
	}
	.library-identity {
		--track-artwork-size: 2.75rem;
	}
	.library-saved {
		display: none;
	}
	.library-actions {
		grid-column: 2 / 3;
		justify-self: start;
	}
	.library-row > :last-child {
		grid-column: 3;
		grid-row: 1;
	}
}
@media (prefers-reduced-motion: reduce) {
	.library-row {
		transition: none;
	}
}
</style>
