<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ProfileLibrary } from "~/core/models/account";

defineProps<{ value: ProfileLibrary }>();
const emit = defineEmits<{
	select: [view: "liked" | "disliked" | "playlists"];
}>();
const { icons } = useTheme();
</script>

<template>
	<section aria-labelledby="profile-library-heading">
		<div class="flex flex-wrap items-end justify-between gap-3">
			<div>
				<p class="text-xs font-medium uppercase tracking-[0.18em] text-primary">Library</p>
				<h2
					id="profile-library-heading"
					class="mt-1 text-lg font-semibold text-highlighted"
				>
					Explicit taste
				</h2>
				<p class="mt-1 text-xs text-muted">
					Deliberate reactions and public collections, separate from listening habits.
				</p>
			</div>
		</div>

		<div class="mt-5 grid gap-3 xl:grid-cols-3">
			<button
				type="button"
				class="library-preview-card group"
				@click="emit('select', 'liked')"
			>
				<span class="flex items-start justify-between gap-4">
					<span>
						<span
							class="flex items-center gap-2 text-sm font-semibold text-highlighted"
						>
							<UIcon :name="icons.like" class="size-4 text-primary" />
							Liked tracks
						</span>
						<span class="mt-1 block text-xs text-muted">
							{{ value.likes_count.toLocaleString() }} saved
						</span>
					</span>
					<UIcon
						:name="icons.arrowRight"
						class="size-4 text-muted transition-transform group-hover:translate-x-0.5"
					/>
				</span>
				<span v-if="value.liked_tracks.length" class="mt-5 grid gap-2 text-left">
					<SharedTrackIdentity
						v-for="track in value.liked_tracks.slice(0, 3)"
						:key="track.track_id"
						:entry="track"
						:title="track.title"
						:artist-names="track.artist_names"
						size="sm"
						multiline
					/>
				</span>
				<span v-else class="mt-8 block text-left text-sm text-muted"
					>No liked tracks yet.</span
				>
			</button>

			<button
				type="button"
				class="library-preview-card group"
				@click="emit('select', 'disliked')"
			>
				<span class="flex items-start justify-between gap-4">
					<span>
						<span
							class="flex items-center gap-2 text-sm font-semibold text-highlighted"
						>
							<UIcon :name="icons.dislike" class="size-4 text-primary" />
							Disliked tracks
						</span>
						<span class="mt-1 block text-xs text-muted">
							{{ value.dislikes_count.toLocaleString() }} saved
						</span>
					</span>
					<UIcon
						:name="icons.arrowRight"
						class="size-4 text-muted transition-transform group-hover:translate-x-0.5"
					/>
				</span>
				<span v-if="value.disliked_tracks.length" class="mt-5 grid gap-2 text-left">
					<SharedTrackIdentity
						v-for="track in value.disliked_tracks.slice(0, 3)"
						:key="track.track_id"
						:entry="track"
						:title="track.title"
						:artist-names="track.artist_names"
						size="sm"
						multiline
					/>
				</span>
				<span v-else class="mt-8 block text-left text-sm text-muted">
					No disliked tracks yet.
				</span>
			</button>

			<button
				type="button"
				class="library-preview-card group"
				@click="emit('select', 'playlists')"
			>
				<span class="flex items-start justify-between gap-4">
					<span>
						<span
							class="flex items-center gap-2 text-sm font-semibold text-highlighted"
						>
							<UIcon :name="icons.library" class="size-4 text-primary" />
							Public playlists
						</span>
						<span class="mt-1 block text-xs text-muted">
							{{ value.public_playlist_count.toLocaleString() }} published
						</span>
					</span>
					<UIcon
						:name="icons.arrowRight"
						class="size-4 text-muted transition-transform group-hover:translate-x-0.5"
					/>
				</span>
				<span v-if="value.public_playlists.length" class="mt-5 grid gap-2 text-left">
					<span
						v-for="playlist in value.public_playlists.slice(0, 3)"
						:key="playlist.playlist_id"
						class="flex min-w-0 items-center gap-2.5"
					>
						<LibraryPlaylistCover
							:artwork-urls="playlist.artwork_urls"
							:name="playlist.name"
							class="size-9 shrink-0 rounded-lg"
						/>
						<span class="min-w-0">
							<span class="block truncate text-sm font-medium text-highlighted">{{
								playlist.name
							}}</span>
							<span class="block text-xs text-muted">
								{{ playlist.entry_count }}
								{{ playlist.entry_count === 1 ? "track" : "tracks" }}
							</span>
						</span>
					</span>
				</span>
				<span v-else class="mt-8 block text-left text-sm text-muted">
					No public playlists yet.
				</span>
			</button>
		</div>
	</section>
</template>

<style scoped>
.library-preview-card {
	min-height: 13rem;
	border: 1px solid color-mix(in srgb, var(--ui-border) 72%, transparent);
	border-radius: 1rem;
	background: color-mix(in srgb, var(--ui-bg-elevated) 36%, transparent);
	padding: 1.25rem;
	text-align: left;
	transition:
		border-color 180ms ease-out,
		background-color 180ms ease-out;
}
.library-preview-card:hover,
.library-preview-card:focus-visible {
	border-color: color-mix(in srgb, var(--ui-primary) 55%, var(--ui-border));
	background: color-mix(in srgb, var(--ui-bg-elevated) 58%, transparent);
}
.library-preview-card:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
	.library-preview-card,
	.library-preview-card :deep(svg) {
		transition: none;
	}
}
</style>
