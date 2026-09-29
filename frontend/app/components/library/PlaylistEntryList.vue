<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import Sortable, { type SortableEvent } from "sortablejs";
import { formatTime } from "~/core/models/player";
import type { PlaylistEntry } from "~/core/models/library";

const props = withDefaults(
	defineProps<{
		items: PlaylistEntry[];
		loading?: boolean;
		skeletonCount?: number;
		editing?: boolean;
		pending?: boolean;
	}>(),
	{ loading: false, skeletonCount: 8, editing: false, pending: false },
);
const emit = defineEmits<{
	remove: [entry: PlaylistEntry];
	order: [entryIds: string[]];
}>();
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const { icons } = useTheme();
const list = ref<HTMLElement | null>(null);
const ordered = ref<PlaylistEntry[]>([]);
let sortable: Sortable | null = null;

watch(
	() => props.items,
	(value) => {
		ordered.value = [...value];
	},
	{ immediate: true },
);

function move(index: number, direction: -1 | 1) {
	const target = index + direction;
	if (target < 0 || target >= ordered.value.length) return;
	const next = [...ordered.value];
	const [entry] = next.splice(index, 1);
	if (!entry) return;
	next.splice(target, 0, entry);
	ordered.value = next;
	sortable?.sort(next.map(({ entry_id }) => entry_id));
	emit(
		"order",
		next.map(({ entry_id }) => entry_id),
	);
}

function finishDrag(event: SortableEvent) {
	if (event.oldDraggableIndex === undefined || event.newDraggableIndex === undefined) return;
	const next = [...ordered.value];
	const [entry] = next.splice(event.oldDraggableIndex, 1);
	if (!entry) return;
	next.splice(event.newDraggableIndex, 0, entry);
	ordered.value = next;
	emit(
		"order",
		next.map(({ entry_id }) => entry_id),
	);
}

onMounted(() => {
	watch(
		() => [list.value, props.editing] as const,
		([element, editing]) => {
			sortable?.destroy();
			sortable =
				element && editing
					? new Sortable(element, {
							draggable: ".playlist-entry",
							handle: ".playlist-entry-handle",
							dataIdAttr: "data-entry-id",
							animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches
								? 0
								: 160,
							ghostClass: "playlist-entry-placeholder",
							onEnd: finishDrag,
						})
					: null;
		},
		{ immediate: true },
	);
});
onBeforeUnmount(() => sortable?.destroy());

function queue(entry: PlaylistEntry) {
	return player.add([{ track_id: entry.track_id, source_id: entry.preferred_source_id }]);
}
</script>

<template>
	<ol v-if="loading" class="playlist-entries" aria-label="Loading playlist tracks">
		<li
			v-for="index in skeletonCount"
			:key="index"
			class="playlist-entry playlist-entry-grid"
			aria-hidden="true"
		>
			<USkeleton class="mx-auto size-4" />
			<SharedTrackIdentity loading class="playlist-identity" />
			<USkeleton class="h-3 w-20" /><USkeleton class="h-3 w-10" />
			<div class="flex gap-1">
				<USkeleton v-for="action in 4" :key="action" class="size-10 rounded-lg" />
			</div>
		</li>
	</ol>
	<ol
		v-else
		ref="list"
		class="playlist-entries"
		:aria-label="editing ? 'Edit playlist order' : 'Playlist tracks'"
	>
		<li
			v-for="(entry, index) in ordered"
			:key="entry.entry_id"
			:data-entry-id="entry.entry_id"
			class="playlist-entry playlist-entry-grid"
		>
			<button
				v-if="editing"
				type="button"
				class="playlist-entry-handle grid size-10 cursor-grab place-items-center text-muted"
				:aria-label="`Drag ${entry.title} to reorder`"
				tabindex="-1"
			>
				<UIcon :name="icons.drag" class="size-4" />
			</button>
			<span v-else class="text-center text-xs tabular-nums text-muted">{{
				String(index + 1).padStart(2, "0")
			}}</span>
			<SharedTrackIdentity
				:entry="entry"
				:title="entry.title"
				:artist-names="entry.artist_names"
				class="playlist-identity"
				multiline
			/>
			<NuxtLink
				:to="`/profile/${entry.added_by.user_id}`"
				class="playlist-contributor flex min-w-0 items-center gap-2 text-xs text-muted hover:text-highlighted"
			>
				<UAvatar
					:src="entry.added_by.avatar_url ?? undefined"
					:alt="entry.added_by.display_name"
					size="2xs"
				/>
				<span class="truncate">{{ entry.added_by.display_name }}</span>
			</NuxtLink>
			<span class="playlist-duration text-xs tabular-nums text-muted">{{
				formatTime(entry.duration_seconds)
			}}</span>
			<div v-if="editing" class="playlist-actions flex items-center justify-end gap-1">
				<UButton
					:icon="icons.arrowUp"
					:aria-label="`Move ${entry.title} up`"
					color="neutral"
					variant="ghost"
					class="size-10 justify-center"
					:disabled="index === 0 || pending"
					@click="move(index, -1)"
				/>
				<UButton
					:icon="icons.arrowDown"
					:aria-label="`Move ${entry.title} down`"
					color="neutral"
					variant="ghost"
					class="size-10 justify-center"
					:disabled="index === ordered.length - 1 || pending"
					@click="move(index, 1)"
				/>
			</div>
			<div v-else class="playlist-actions flex items-center justify-end gap-1">
				<LibraryReactionActions
					:track-id="entry.track_id"
					:title="entry.title"
					compact
					:show-counts="false"
					:show-details="false"
					mode="menu"
				/>
				<LibraryPlaylistAction
					:track="{
						track_id: entry.track_id,
						preferred_source_id: entry.preferred_source_id,
					}"
					:title="entry.title"
				/>
				<UTooltip :text="`Add ${entry.title} to queue`"
					><UButton
						:icon="icons.plus"
						:aria-label="`Add ${entry.title} to queue`"
						color="neutral"
						variant="ghost"
						class="size-10 justify-center"
						:disabled="!player.canControl"
						:loading="player.isPending('queue.add')"
						@click="queue(entry)"
				/></UTooltip>
				<UTooltip :text="`Remove ${entry.title} from playlist`"
					><UButton
						:icon="icons.trash"
						:aria-label="`Remove ${entry.title} from playlist`"
						color="error"
						variant="ghost"
						class="size-10 justify-center"
						:disabled="pending"
						@click="emit('remove', entry)"
				/></UTooltip>
			</div>
		</li>
	</ol>
</template>

<style scoped>
.playlist-entry-grid {
	display: grid;
	grid-template-columns: 2.5rem minmax(12rem, 1fr) minmax(8rem, 12rem) 3.5rem minmax(12rem, auto);
	align-items: center;
	gap: 1rem;
}
.playlist-entry {
	min-height: 4.75rem;
	padding: 0.75rem 0.5rem;
	border-radius: 0.5rem;
	transition:
		background-color 140ms ease-out,
		transform 160ms ease-out;
}
.playlist-entry:hover,
.playlist-entry:focus-within {
	background: var(--ui-bg-muted);
}
.playlist-entry-placeholder {
	opacity: 0.35;
}
@container workspace (max-width: 760px) {
	.playlist-entry-grid {
		grid-template-columns: 2rem minmax(0, 1fr);
		gap: 0.75rem;
		padding-inline: 0;
	}
	.playlist-contributor,
	.playlist-duration {
		display: none;
	}
	.playlist-actions {
		grid-column: 2 / -1;
		justify-content: flex-start;
	}
}
@media (prefers-reduced-motion: reduce) {
	.playlist-entry {
		transition: none;
	}
}
</style>
