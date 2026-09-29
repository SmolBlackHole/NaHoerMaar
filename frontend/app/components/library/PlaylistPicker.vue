<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import { failureMessage } from "~/core/errors";
import type { Playlist, PlaylistTrack } from "~/core/models/library";

const props = withDefaults(
	defineProps<{
		tracks: PlaylistTrack[];
		label?: string;
		compact?: boolean;
		disabled?: boolean;
	}>(),
	{ label: "Add to playlist", compact: false, disabled: false },
);
const emit = defineEmits<{ added: [playlist: Playlist] }>();
const core = useNuxtApp().$backendCore;
const libraryStore = core.stores.useLibraryStore();
const { icons } = useTheme();
const toast = useToast();
const open = ref(false);
const loading = ref(false);
const savingId = ref<string | null>(null);
const error = ref<string | null>(null);
const playlists = ref<Playlist[]>([]);
const name = ref("");
const creating = ref(false);

async function load() {
	loading.value = true;
	error.value = null;
	try {
		const page = await core.client.library.playlists({ page: 1, pageSize: 100 });
		playlists.value = page.items;
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		loading.value = false;
	}
}

watch(open, (value) => {
	if (value) void load();
	else {
		name.value = "";
		creating.value = false;
		error.value = null;
	}
});

async function addTo(playlist: Playlist) {
	if (!props.tracks.length || savingId.value) return;
	savingId.value = playlist.playlist_id;
	error.value = null;
	try {
		const updated = await core.client.library.addPlaylistEntries(
			playlist.playlist_id,
			props.tracks,
			playlist.revision,
		);
		toast.add({
			title: props.tracks.length === 1 ? "Added to playlist" : "Tracks added",
			description: `${props.tracks.length} ${props.tracks.length === 1 ? "track" : "tracks"} added to ${updated.name}.`,
			color: "success",
		});
		libraryStore.invalidatePlaylists();
		emit("added", updated);
		open.value = false;
	} catch (failure) {
		error.value = failureMessage(failure);
		await load();
	} finally {
		savingId.value = null;
	}
}

async function createAndAdd() {
	const value = name.value.trim();
	if (!value || savingId.value) return;
	savingId.value = "new";
	error.value = null;
	try {
		const created = await core.client.library.createPlaylist(value);
		playlists.value = [created, ...playlists.value];
		savingId.value = null;
		await addTo(created);
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		if (savingId.value === "new") savingId.value = null;
	}
}
</script>

<template>
	<UButton
		:label="compact ? undefined : label"
		:icon="icons.folder"
		:aria-label="label"
		color="neutral"
		variant="ghost"
		:class="compact ? 'size-10 justify-center' : ''"
		:disabled="disabled || !tracks.length"
		@click="open = true"
	/>

	<UModal
		:open="open"
		title="Add to playlist"
		:description="`${tracks.length} ${tracks.length === 1 ? 'track' : 'tracks'} selected. Repeated tracks stay repeated.`"
		:ui="{ content: 'sm:max-w-xl', body: 'p-0 sm:p-0' }"
		@update:open="open = $event"
	>
		<template #body>
			<div class="min-h-80">
				<div
					class="flex items-center justify-between gap-3 border-b border-default px-4 py-3 sm:px-6"
				>
					<p class="text-sm text-muted">Choose a playlist or create one here.</p>
					<UButton
						:label="creating ? 'Cancel' : 'New playlist'"
						:icon="creating ? icons.close : icons.plus"
						color="neutral"
						variant="soft"
						@click="creating = !creating"
					/>
				</div>
				<form
					v-if="creating"
					class="flex gap-2 border-b border-default px-4 py-4 sm:px-6"
					@submit.prevent="createAndAdd"
				>
					<UInput
						v-model="name"
						maxlength="100"
						autofocus
						placeholder="Playlist name"
						class="min-w-0 flex-1"
					/>
					<UButton
						type="submit"
						label="Create and add"
						:icon="icons.plus"
						:loading="savingId === 'new'"
						:disabled="!name.trim()"
					/>
				</form>
				<p v-if="error" class="px-4 py-3 text-sm text-warning sm:px-6" role="alert">
					{{ error }}
				</p>
				<ul v-if="loading" class="divide-y divide-default" aria-label="Loading playlists">
					<li
						v-for="index in 4"
						:key="index"
						class="flex items-center gap-3 px-4 py-3 sm:px-6"
						aria-hidden="true"
					>
						<USkeleton class="size-12 rounded-xl" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-40" /><USkeleton class="h-3 w-20" />
						</div>
						<USkeleton class="size-9 rounded-lg" />
					</li>
				</ul>
				<div
					v-else-if="!playlists.length"
					class="grid min-h-56 place-items-center px-6 text-center"
				>
					<div>
						<UIcon :name="icons.folder" class="mx-auto size-9 text-muted" />
						<p class="mt-3 font-medium text-highlighted">No playlists yet</p>
						<p class="mt-1 text-sm text-muted">Create one to save these tracks.</p>
					</div>
				</div>
				<ul v-else class="max-h-[26rem] divide-y divide-default overflow-y-auto">
					<li v-for="playlist in playlists" :key="playlist.playlist_id">
						<button
							type="button"
							class="flex min-h-18 w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-elevated focus-visible:outline-2 focus-visible:outline-primary sm:px-6"
							:disabled="Boolean(savingId)"
							@click="addTo(playlist)"
						>
							<LibraryPlaylistCover
								:artwork-urls="playlist.artwork_urls"
								:name="playlist.name"
								class="size-12"
							/>
							<span class="min-w-0 flex-1"
								><span
									class="block truncate text-sm font-medium text-highlighted"
									>{{ playlist.name }}</span
								><span class="mt-1 block text-xs text-muted"
									>{{ playlist.entry_count }}
									{{ playlist.entry_count === 1 ? "track" : "tracks" }}</span
								></span
							>
							<UIcon
								:name="
									savingId === playlist.playlist_id ? icons.loading : icons.plus
								"
								class="size-5 shrink-0"
								:class="{ 'animate-spin': savingId === playlist.playlist_id }"
							/>
						</button>
					</li>
				</ul>
			</div>
		</template>
	</UModal>
</template>
