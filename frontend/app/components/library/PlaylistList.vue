<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ContextMenuItem } from "@nuxt/ui";
import type { Playlist } from "~/core/models/library";

const props = withDefaults(
	defineProps<{ items: Playlist[]; loading?: boolean; skeletonCount?: number }>(),
	{ loading: false, skeletonCount: 6 },
);
const emit = defineEmits<{
	select: [playlist: Playlist];
	queue: [playlist: Playlist];
	rename: [playlist: Playlist];
	duplicate: [playlist: Playlist];
	delete: [playlist: Playlist];
}>();
const { icons } = useTheme();

function contextItems(playlist: Playlist): ContextMenuItem[][] {
	return [
		[
			{
				label: "Open",
				icon: icons.value.arrowRight,
				onSelect: () => emit("select", playlist),
			},
			{
				label: "Add to queue",
				icon: icons.value.play,
				disabled: !playlist.entry_count || playlist.entry_count > 100,
				onSelect: () => emit("queue", playlist),
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
	<ul class="playlist-grid" :aria-label="loading ? 'Loading playlists' : 'Playlists'">
		<li
			v-for="index in loading ? skeletonCount : 0"
			:key="`skeleton-${index}`"
			aria-hidden="true"
			class="playlist-card"
		>
			<USkeleton class="aspect-square w-full rounded-2xl" />
			<div class="mt-4 space-y-2">
				<USkeleton class="h-5 w-2/3" /><USkeleton class="h-3 w-24" />
			</div>
		</li>
		<UContextMenu
			v-for="playlist in props.items"
			:key="playlist.playlist_id"
			:items="contextItems(playlist)"
		>
			<li>
				<button
					type="button"
					class="playlist-card group w-full text-left"
					@click="emit('select', playlist)"
				>
					<div class="relative">
						<LibraryPlaylistCover
							:artwork-urls="playlist.artwork_urls"
							:name="playlist.name"
							class="playlist-card-cover aspect-square w-full rounded-2xl"
						/>
						<span
							class="absolute bottom-3 right-3 grid size-10 place-items-center rounded-full bg-primary text-inverted opacity-0 shadow-lg transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100"
						>
							<UIcon :name="icons.arrowRight" class="size-5" />
						</span>
					</div>
					<span class="mt-4 block truncate text-base font-semibold text-highlighted">{{
						playlist.name
					}}</span>
					<span class="mt-1 block text-xs text-muted"
						>{{ playlist.entry_count }}
						{{ playlist.entry_count === 1 ? "track" : "tracks" }}</span
					>
				</button>
			</li>
		</UContextMenu>
	</ul>
</template>

<style scoped>
.playlist-grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr));
	gap: 1.5rem;
}
.playlist-card {
	min-width: 0;
	border: 1px solid transparent;
	border-radius: 1rem;
	padding: 0.5rem;
	transition: border-color 180ms ease-out;
}
.playlist-card:hover,
.playlist-card:focus-visible {
	border-color: color-mix(in srgb, var(--ui-primary) 55%, var(--ui-border));
}
.playlist-card:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 0.35rem;
}
.playlist-card-cover {
	filter: saturate(0.68);
	transition: filter 180ms ease-out;
}
.playlist-card:hover .playlist-card-cover,
.playlist-card:focus-visible .playlist-card-cover {
	filter: saturate(1);
}
@media (prefers-reduced-motion: reduce) {
	.playlist-card,
	.playlist-card-cover {
		transition: none;
	}
}
</style>
