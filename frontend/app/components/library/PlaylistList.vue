<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ContextMenuItem } from "@nuxt/ui";
import Sortable, { type SortableEvent } from "sortablejs";
import type { Playlist } from "~/core/models/library";

const props = withDefaults(
	defineProps<{
		items: Playlist[];
		loading?: boolean;
		skeletonCount?: number;
		reorderable?: boolean;
		pending?: boolean;
		total?: number;
	}>(),
	{ loading: false, skeletonCount: 6, reorderable: false, pending: false, total: 0 },
);
const emit = defineEmits<{
	select: [playlist: Playlist];
	rename: [playlist: Playlist];
	duplicate: [playlist: Playlist];
	delete: [playlist: Playlist];
	move: [playlistId: string, position: number];
}>();
const { icons } = useTheme();
const list = ref<HTMLElement | null>(null);
const ordered = ref<Playlist[]>([]);
let sortable: Sortable | null = null;

watch(
	() => props.items,
	(value) => {
		ordered.value = [...value];
	},
	{ immediate: true },
);

function optimisticOrder(items: Playlist[]) {
	const firstPosition = Math.min(...items.map(({ position }) => position));
	return items.map((playlist, index) => ({
		...playlist,
		position: firstPosition + index,
	}));
}

function move(index: number, direction: -1 | 1) {
	const playlist = ordered.value[index];
	if (!playlist || props.pending) return;
	const position = playlist.position + direction;
	if (position < 0 || position >= props.total) return;
	const target = index + direction;
	if (target >= 0 && target < ordered.value.length) {
		const next = [...ordered.value];
		next.splice(index, 1);
		next.splice(target, 0, playlist);
		ordered.value = optimisticOrder(next);
		sortable?.sort(ordered.value.map(({ playlist_id }) => playlist_id));
	}
	emit("move", playlist.playlist_id, position);
}

function finishDrag(event: SortableEvent) {
	if (event.oldDraggableIndex === undefined || event.newDraggableIndex === undefined) return;
	if (event.oldDraggableIndex === event.newDraggableIndex) return;
	const next = [...ordered.value];
	const [playlist] = next.splice(event.oldDraggableIndex, 1);
	if (!playlist) return;
	next.splice(event.newDraggableIndex, 0, playlist);
	ordered.value = optimisticOrder(next);
	emit("move", playlist.playlist_id, ordered.value[event.newDraggableIndex]!.position);
}

onMounted(() => {
	watch(
		() => [list.value, props.reorderable, props.pending] as const,
		([element, reorderable, pending]) => {
			sortable?.destroy();
			sortable =
				element && reorderable && !pending
					? new Sortable(element, {
							draggable: ".playlist-card-shell",
							handle: ".playlist-reorder-handle",
							dataIdAttr: "data-playlist-id",
							animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches
								? 0
								: 160,
							ghostClass: "playlist-card-placeholder",
							onEnd: finishDrag,
						})
					: null;
		},
		{ immediate: true },
	);
});
onBeforeUnmount(() => sortable?.destroy());

function contextItems(playlist: Playlist): ContextMenuItem[][] {
	return [
		[
			{
				label: "Open",
				icon: icons.value.arrowRight,
				onSelect: () => emit("select", playlist),
			},
		],
		[
			{
				label: "Rename",
				icon: icons.value.type,
				disabled: playlist.access !== "owner",
				onSelect: () => emit("rename", playlist),
			},
			{
				label: "Duplicate",
				icon: icons.value.copy,
				disabled: playlist.access === "reader",
				onSelect: () => emit("duplicate", playlist),
			},
			{
				label: "Delete",
				icon: icons.value.trash,
				color: "error",
				disabled: playlist.access !== "owner",
				onSelect: () => emit("delete", playlist),
			},
		],
	];
}
</script>

<template>
	<ul ref="list" class="playlist-grid" :aria-label="loading ? 'Loading playlists' : 'Playlists'">
		<li
			v-for="index in loading ? skeletonCount : 0"
			:key="`skeleton-${index}`"
			aria-hidden="true"
			class="playlist-card-shell"
		>
			<article class="playlist-card">
				<USkeleton
					v-if="reorderable"
					class="playlist-reorder-skeleton size-10 rounded-lg"
				/>
				<USkeleton class="aspect-square w-full rounded-2xl" />
				<div class="mt-4 space-y-2">
					<USkeleton class="h-5 w-2/3" /><USkeleton class="h-3 w-24" />
				</div>
			</article>
		</li>
		<li
			v-for="(playlist, index) in ordered"
			:key="playlist.playlist_id"
			:data-playlist-id="playlist.playlist_id"
			class="playlist-card-shell"
		>
			<UContextMenu :items="contextItems(playlist)">
				<article class="playlist-card group">
					<button
						v-if="reorderable"
						type="button"
						class="playlist-reorder-handle"
						:disabled="pending"
						:aria-label="`Reorder ${playlist.name}`"
						aria-description="Drag to reorder. Use the up and down arrow keys for precise moves."
						aria-keyshortcuts="ArrowUp ArrowDown"
						@keydown.up.prevent="move(index, -1)"
						@keydown.down.prevent="move(index, 1)"
					>
						<UIcon :name="icons.drag" class="size-5" />
					</button>
					<button
						type="button"
						class="playlist-card-open w-full text-left"
						@click="emit('select', playlist)"
					>
						<div class="relative">
							<LibraryPlaylistCover
								:artwork-urls="playlist.artwork_urls"
								:name="playlist.name"
								class="playlist-card-cover aspect-square w-full rounded-2xl"
							/>
							<span
								class="absolute bottom-3 right-3 grid size-10 place-items-center rounded-full bg-primary text-inverted opacity-0 shadow-lg transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100"
							>
								<UIcon :name="icons.arrowRight" class="size-5" />
							</span>
						</div>
						<span
							class="mt-4 block truncate text-base font-semibold text-highlighted"
							>{{ playlist.name }}</span
						>
						<span class="mt-1 block text-xs text-muted"
							>{{ playlist.entry_count }}
							{{ playlist.entry_count === 1 ? "track" : "tracks" }}</span
						>
					</button>
				</article>
			</UContextMenu>
		</li>
	</ul>
</template>

<style scoped>
.playlist-grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr));
	gap: 1.5rem;
}
.playlist-card-shell {
	min-width: 0;
}
.playlist-card {
	position: relative;
	min-width: 0;
	border: 1px solid transparent;
	border-radius: 1rem;
	padding: 0.5rem;
	transition: border-color 180ms ease-out;
}
.playlist-card:hover,
.playlist-card:focus-within {
	border-color: color-mix(in srgb, var(--ui-primary) 55%, var(--ui-border));
}
.playlist-card:focus-within {
	outline: 2px solid var(--ui-primary);
	outline-offset: 0.35rem;
}
.playlist-card-open:focus-visible {
	outline: none;
}
.playlist-card-cover {
	filter: saturate(0.68);
	transition: filter 180ms ease-out;
}
.playlist-card:hover .playlist-card-cover,
.playlist-card:focus-within .playlist-card-cover {
	filter: saturate(1);
}
.playlist-reorder-handle,
.playlist-reorder-skeleton {
	position: absolute;
	top: 0.75rem;
	left: 0.75rem;
	z-index: 2;
}
.playlist-reorder-handle {
	display: grid;
	width: 2.5rem;
	height: 2.5rem;
	place-items: center;
	border: 1px solid color-mix(in srgb, var(--ui-border) 72%, transparent);
	border-radius: 0.625rem;
	background: color-mix(in srgb, var(--ui-bg) 88%, transparent);
	color: var(--ui-text-muted);
	cursor: grab;
	touch-action: none;
}
.playlist-reorder-handle:hover,
.playlist-reorder-handle:focus-visible {
	color: var(--ui-text-highlighted);
	border-color: color-mix(in srgb, var(--ui-primary) 55%, var(--ui-border));
}
.playlist-reorder-handle:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
.playlist-reorder-handle:active:not(:disabled) {
	cursor: grabbing;
}
.playlist-reorder-handle:disabled {
	cursor: default;
	opacity: 0.55;
}
.playlist-card-placeholder {
	opacity: 0.35;
}
@media (prefers-reduced-motion: reduce) {
	.playlist-card,
	.playlist-card-cover,
	.playlist-card-open span {
		transition: none;
	}
}
</style>
