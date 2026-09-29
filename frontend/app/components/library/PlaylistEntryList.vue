<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ContextMenuItem } from "@nuxt/ui";
import Sortable, { type SortableEvent } from "sortablejs";
import { formatTime } from "~/core/models/player";
import type { PlaylistEntry } from "~/core/models/library";

const props = withDefaults(
	defineProps<{
		items: PlaylistEntry[];
		loading?: boolean;
		skeletonCount?: number;
		reorderable?: boolean;
		pending?: boolean;
		total?: number;
		selectable?: boolean;
		selectedEntryIds?: Set<string>;
		editable?: boolean;
	}>(),
	{
		loading: false,
		skeletonCount: 8,
		reorderable: false,
		pending: false,
		total: 0,
		selectable: false,
		selectedEntryIds: () => new Set(),
		editable: true,
	},
);
const emit = defineEmits<{
	remove: [entry: PlaylistEntry];
	move: [entryId: string, position: number];
	toggle: [entry: PlaylistEntry];
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
	const current = ordered.value[index];
	if (!current) return;
	const position = current.position + direction;
	if (position < 0 || position >= props.total) return;
	const target = index + direction;
	if (target < 0 || target >= ordered.value.length) {
		emit("move", current.entry_id, position);
		return;
	}
	const next = [...ordered.value];
	const [entry] = next.splice(index, 1);
	if (!entry) return;
	next.splice(target, 0, entry);
	const firstPosition = Math.min(...next.map(({ position: value }) => value));
	ordered.value = next.map((value, nextIndex) => ({
		...value,
		position: firstPosition + nextIndex,
	}));
	sortable?.sort(next.map(({ entry_id }) => entry_id));
	emit("move", entry.entry_id, position);
}

function finishDrag(event: SortableEvent) {
	if (event.oldDraggableIndex === undefined || event.newDraggableIndex === undefined) return;
	if (event.oldDraggableIndex === event.newDraggableIndex) return;
	const next = [...ordered.value];
	const [entry] = next.splice(event.oldDraggableIndex, 1);
	if (!entry) return;
	next.splice(event.newDraggableIndex, 0, entry);
	const firstPosition = Math.min(...next.map(({ position }) => position));
	ordered.value = next.map((value, index) => ({
		...value,
		position: firstPosition + index,
	}));
	emit("move", entry.entry_id, firstPosition + event.newDraggableIndex);
}

onMounted(() => {
	watch(
		() => [list.value, props.reorderable, props.pending] as const,
		([element, reorderable, pending]) => {
			sortable?.destroy();
			sortable =
				element && reorderable && !pending
					? new Sortable(element, {
							draggable: ".playlist-entry",
							handle: ".track-reorder-handle",
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

function contextItems(entry: PlaylistEntry, index: number): ContextMenuItem[][] {
	const groups: ContextMenuItem[][] = [
		[
			{
				label: "Add to queue",
				icon: icons.value.plus,
				disabled: !player.canControl,
				onSelect: () => void queue(entry),
			},
			...(props.editable
				? [
						{
							label: "Move up",
							icon: icons.value.arrowUp,
							disabled: !props.reorderable || props.pending || entry.position === 0,
							onSelect: () => move(index, -1),
						} satisfies ContextMenuItem,
						{
							label: "Move down",
							icon: icons.value.arrowDown,
							disabled:
								!props.reorderable ||
								props.pending ||
								entry.position >= props.total - 1,
							onSelect: () => move(index, 1),
						} satisfies ContextMenuItem,
					]
				: []),
		],
	];
	if (props.editable)
		groups.push([
			{
				label: "Remove from playlist",
				icon: icons.value.trash,
				color: "error",
				disabled: props.pending,
				onSelect: () => emit("remove", entry),
			},
		]);
	return groups;
}
</script>

<template>
	<ol v-if="loading" class="playlist-entries" aria-label="Loading playlist tracks">
		<SharedTrackRow
			v-for="index in skeletonCount"
			:key="index"
			class="playlist-entry playlist-entry-grid"
			identity-class="playlist-identity"
			:position="index"
			:reorderable="editable && reorderable"
			loading
		>
			<template v-if="selectable" #leading>
				<div class="grid size-10 shrink-0 place-items-center">
					<USkeleton class="size-4" />
				</div>
			</template>
			<template #details>
				<USkeleton class="playlist-contributor h-3 w-20" />
				<USkeleton class="playlist-duration h-3 w-10" />
			</template>
			<template #actions>
				<div class="playlist-actions flex gap-1">
					<USkeleton
						v-for="action in editable ? 4 : 3"
						:key="action"
						class="size-10 rounded-lg"
					/>
				</div>
			</template>
		</SharedTrackRow>
	</ol>
	<ol v-else ref="list" class="playlist-entries" aria-label="Playlist tracks">
		<SharedTrackRow
			v-for="(entry, index) in ordered"
			:key="entry.entry_id"
			:data-entry-id="entry.entry_id"
			class="playlist-entry playlist-entry-grid"
			identity-class="playlist-identity"
			:context-items="contextItems(entry, index)"
			:entry="entry"
			:title="entry.title"
			:artist-names="entry.artist_names"
			:position="entry.position + 1"
			:reorderable="editable && reorderable"
			:reorder-disabled="pending"
			multiline
			@move="move(index, $event)"
		>
			<template v-if="selectable" #leading>
				<label class="playlist-select grid size-10 shrink-0 place-items-center">
					<input
						type="checkbox"
						:checked="selectedEntryIds.has(entry.entry_id)"
						@change="emit('toggle', entry)"
					/>
					<span class="sr-only">Select {{ entry.title }}</span>
				</label>
			</template>
			<template #details>
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
			</template>
			<template #actions>
				<div class="playlist-actions flex items-center justify-end gap-1">
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
					<UTooltip v-if="editable" :text="`Remove ${entry.title} from playlist`"
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
			</template>
		</SharedTrackRow>
	</ol>
</template>

<style scoped>
:deep(.playlist-entry-grid) {
	display: grid;
	grid-template-columns: minmax(14rem, 1fr) minmax(8rem, 12rem) 3.5rem minmax(12rem, auto);
	align-items: center;
	gap: 1rem;
}
:deep(.playlist-entry-placeholder) {
	opacity: 0.35;
}
.playlist-select input {
	width: 1rem;
	height: 1rem;
	accent-color: var(--ui-primary);
}
@container workspace (max-width: 760px) {
	:deep(.playlist-entry-grid) {
		grid-template-columns: minmax(0, 1fr);
		gap: 0.75rem;
		padding-inline: 0;
	}
	:deep(.playlist-identity) {
		--track-artwork-size: 2.75rem;
	}
	.playlist-contributor,
	.playlist-duration {
		display: none;
	}
	.playlist-actions {
		grid-column: 1 / -1;
		justify-content: flex-start;
	}
}
</style>
