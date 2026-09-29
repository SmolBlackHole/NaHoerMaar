<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import { failureMessage } from "~/core/errors";
import type { Playlist, PlaylistEntry, ReactionValue } from "~/core/models/library";

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Library | NaHörMaar" });

type LibraryView = "liked" | "disliked" | "playlists";
const PAGE_SIZE = 25;
const core = useNuxtApp().$backendCore;
const library = core.workflows.library();
const reactions = core.stores.useLibraryStore();
const player = core.stores.usePlayerStore();
const route = useRoute();
const router = useRouter();
const { icons } = useTheme();
const toast = useToast();
const search = ref("");
const currentPage = ref(1);
const mounted = ref(false);
const playlist = ref<Playlist | null>(null);
const playlistLoading = ref(false);
const actionPending = ref(false);
const actionError = ref<string | null>(null);
const createOpen = ref(false);
const deleteOpen = ref(false);
const playlistName = ref("");
const renaming = ref(false);
const renameInput = ref<{ inputRef?: HTMLInputElement } | null>(null);
const selectingEntries = ref(false);
const selectedEntryIds = ref<Set<string>>(new Set());

const trackResult = computed(() => library.tracks.data.value);
const playlistResult = computed(() => library.playlists.data.value);
const entryResult = computed(() => library.entries.data.value);
const selectedPlaylistId = computed(() => queryValue(route.query.playlist));
const libraryViewKey = computed(() =>
	selectedPlaylistId.value ? `playlist:${selectedPlaylistId.value}` : selectedView(),
);
const canReorderEntries = computed(
	() =>
		Boolean(playlist.value) &&
		playlist.value?.access !== "reader" &&
		!playlist.value?.source &&
		!selectedQuery() &&
		!selectingEntries.value,
);
const selectedEntries = computed(() =>
	(entryResult.value?.items ?? []).filter(({ entry_id }) => selectedEntryIds.value.has(entry_id)),
);
const activeResult = computed(() => {
	if (selectedView() !== "playlists") return trackResult.value;
	return selectedPlaylistId.value ? entryResult.value : playlistResult.value;
});
const initialLoading = computed(() => {
	if (selectedView() !== "playlists") return library.tracks.loading.value && !trackResult.value;
	if (!selectedPlaylistId.value) return library.playlists.loading.value && !playlistResult.value;
	return playlistLoading.value || (library.entries.loading.value && !entryResult.value);
});

function queryValue(value: unknown) {
	return typeof value === "string" ? value : "";
}
function selectedView(): LibraryView {
	const value = queryValue(route.query.view);
	return value === "liked" || value === "disliked" ? value : "playlists";
}
function selectedReaction(): ReactionValue {
	return selectedView() === "disliked" ? "dislike" : "like";
}
function selectedPage() {
	const page = Number(queryValue(route.query.page));
	return Number.isInteger(page) && page > 0 ? page : 1;
}
function selectedQuery() {
	return queryValue(route.query.q).trim();
}
function routeQuery(
	overrides: {
		view?: LibraryView;
		playlist?: string;
		q?: string;
		page?: number;
	} = {},
) {
	const view = overrides.view ?? selectedView();
	const playlistId = overrides.playlist ?? selectedPlaylistId.value;
	const q = overrides.q ?? selectedQuery();
	const page = overrides.page ?? selectedPage();
	return {
		...(view !== "playlists" ? { view } : {}),
		...(view === "playlists" && playlistId ? { playlist: playlistId } : {}),
		...(q ? { q } : {}),
		...(page > 1 ? { page: String(page) } : {}),
	};
}

async function normalizePage(loadedPage: number, requestedPage: number) {
	currentPage.value = loadedPage;
	if (loadedPage !== requestedPage)
		await router.replace({ path: "/library", query: routeQuery({ page: loadedPage }) });
}
async function load(newSnapshot = false) {
	const page = selectedPage();
	currentPage.value = page;
	search.value = selectedQuery();
	actionError.value = null;
	if (selectedView() !== "playlists") {
		playlist.value = null;
		library.clearEntries();
		const loaded = await library.loadTracks({
			page,
			pageSize: PAGE_SIZE,
			query: selectedQuery(),
			filters: { reaction: selectedReaction() },
			newSnapshot,
		});
		if (!loaded) return;
		reactions.ingest(loaded.items);
		await normalizePage(loaded.page, page);
		return;
	}
	const playlistId = selectedPlaylistId.value;
	if (!playlistId) {
		playlist.value = null;
		library.clearEntries();
		const loaded = await library.loadPlaylists({
			page,
			pageSize: PAGE_SIZE,
			query: selectedQuery(),
			newSnapshot,
		});
		if (loaded) await normalizePage(loaded.page, page);
		return;
	}
	playlistLoading.value = true;
	try {
		playlist.value = await core.client.library.playlist(playlistId);
	} catch (failure) {
		actionError.value = failureMessage(failure);
		playlist.value = null;
	} finally {
		playlistLoading.value = false;
	}
	if (!playlist.value) return;
	const loaded = await library.loadEntries(playlistId, {
		page,
		pageSize: PAGE_SIZE,
		query: selectedQuery(),
		newSnapshot,
	});
	if (!loaded) return;
	selectedEntryIds.value = new Set(
		[...selectedEntryIds.value].filter((id) =>
			loaded.items.some(({ entry_id }) => entry_id === id),
		),
	);
	await normalizePage(loaded.page, page);
}

async function chooseView(view: LibraryView) {
	await router.push({
		path: "/library",
		query: routeQuery({ view, playlist: "", q: "", page: 1 }),
	});
}
async function openPlaylist(value: Playlist) {
	await router.push({
		path: "/library",
		query: routeQuery({
			view: "playlists",
			playlist: value.playlist_id,
			q: "",
			page: 1,
		}),
	});
}
async function editPlaylist(value: Playlist) {
	await openPlaylist(value);
	await beginRename(value);
}
async function searchLibrary() {
	await router.push({ path: "/library", query: routeQuery({ q: search.value.trim(), page: 1 }) });
}
async function clearSearch() {
	search.value = "";
	await router.push({ path: "/library", query: routeQuery({ q: "", page: 1 }) });
}
async function selectPage(page: number) {
	await router.push({ path: "/library", query: routeQuery({ page }) });
}

async function createPlaylist() {
	const name = playlistName.value.trim();
	if (!name || actionPending.value) return;
	actionPending.value = true;
	try {
		const created = await core.client.library.createPlaylist(name);
		createOpen.value = false;
		playlistName.value = "";
		toast.add({ title: "Playlist created", description: created.name, color: "success" });
		await openPlaylist(created);
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}
async function renamePlaylist() {
	const current = playlist.value;
	const name = playlistName.value.trim();
	if (!current || !name || actionPending.value) return;
	actionPending.value = true;
	try {
		playlist.value = await core.client.library.renamePlaylist(
			current.playlist_id,
			name,
			current.revision,
		);
		renaming.value = false;
		playlistName.value = "";
		toast.add({ title: "Playlist renamed", color: "success" });
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}
async function beginRename(value = playlist.value) {
	if (!value || value.access !== "owner") return;
	playlistName.value = value.name;
	renaming.value = true;
	await nextTick();
	renameInput.value?.inputRef?.focus();
	renameInput.value?.inputRef?.select();
}
function cancelRename() {
	renaming.value = false;
	playlistName.value = "";
}
async function duplicatePlaylist(value = playlist.value) {
	const current = value;
	if (!current || actionPending.value) return;
	actionPending.value = true;
	try {
		const duplicate = await core.client.library.duplicatePlaylist(
			current.playlist_id,
			current.revision,
		);
		toast.add({ title: "Playlist duplicated", description: duplicate.name, color: "success" });
		await openPlaylist(duplicate);
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}
async function deletePlaylist() {
	const current = playlist.value;
	if (!current || actionPending.value) return;
	actionPending.value = true;
	try {
		await core.client.library.deletePlaylist(current.playlist_id, current.revision);
		deleteOpen.value = false;
		toast.add({ title: "Playlist deleted", color: "success" });
		await chooseView("playlists");
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}
function confirmDelete(value = playlist.value) {
	if (!value || value.access !== "owner") return;
	playlist.value = value;
	deleteOpen.value = true;
}
async function removeEntry(entry: PlaylistEntry) {
	const current = playlist.value;
	if (!current || actionPending.value) return;
	actionPending.value = true;
	try {
		playlist.value = await core.client.library.deletePlaylistEntry(
			current.playlist_id,
			entry.entry_id,
			current.revision,
		);
		toast.add({ title: "Track removed", description: entry.title, color: "success" });
		await load(true);
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}
function toggleEntry(entry: PlaylistEntry) {
	const next = new Set(selectedEntryIds.value);
	if (next.has(entry.entry_id)) next.delete(entry.entry_id);
	else next.add(entry.entry_id);
	selectedEntryIds.value = next;
}
function finishEntrySelection() {
	selectedEntryIds.value = new Set();
	selectingEntries.value = false;
}
async function queueSelected() {
	if (!selectedEntries.value.length || actionPending.value) return;
	actionPending.value = true;
	try {
		await player.add(
			selectedEntries.value.map((entry) => ({
				track_id: entry.track_id,
				source_id: entry.preferred_source_id,
			})),
		);
		toast.add({
			title: "Tracks added to queue",
			description: `${selectedEntries.value.length} selected tracks added.`,
			color: "success",
		});
		finishEntrySelection();
	} finally {
		actionPending.value = false;
	}
}
async function moveEntry(entryId: string, position: number) {
	const current = playlist.value;
	if (!current || actionPending.value) return;
	actionPending.value = true;
	try {
		playlist.value = await core.client.library.movePlaylistEntry(
			current.playlist_id,
			entryId,
			position,
			current.revision,
		);
		await load(true);
	} catch (failure) {
		actionError.value = failureMessage(failure);
		await load(true);
	} finally {
		actionPending.value = false;
	}
}

const playlistMenu = computed<DropdownMenuItem[][]>(() => [
	[{ label: "Duplicate", icon: icons.value.copy, onSelect: () => duplicatePlaylist() }],
	[
		{
			label: "Delete",
			icon: icons.value.trash,
			color: "error",
			disabled: playlist.value?.access !== "owner",
			onSelect: () => confirmDelete(),
		},
	],
]);

watch(
	() => route.fullPath,
	() => {
		if (mounted.value) void load();
	},
);
watch(
	() => reactions.revision,
	() => {
		if (mounted.value && selectedView() !== "playlists") void load(true);
	},
);
watch(
	() => reactions.playlistRevision,
	() => {
		if (mounted.value && selectedView() === "playlists") void load(true);
	},
);
onMounted(() => {
	mounted.value = true;
	void load(true);
});
onScopeDispose(library.dispose);
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1">
		<UDashboardPanel
			id="library"
			class="min-h-0 min-w-0 flex-1"
			:ui="{ body: 'pt-6 sm:pt-6 pb-5 sm:pb-5' }"
		>
			<template #header>
				<UDashboardNavbar :ui="{ left: 'h-full gap-3 sm:gap-4' }">
					<template #left>
						<UDashboardSidebarCollapse />
						<h1 class="sr-only">Library</h1>
						<div
							class="inline-flex rounded-lg bg-elevated p-1"
							role="group"
							aria-label="Library view"
						>
							<UButton
								:icon="icons.like"
								aria-label="Liked tracks"
								:color="selectedView() === 'liked' ? 'primary' : 'neutral'"
								:variant="selectedView() === 'liked' ? 'soft' : 'ghost'"
								:aria-pressed="selectedView() === 'liked'"
								@click="chooseView('liked')"
							>
								<span class="hidden sm:inline">Liked</span>
							</UButton>
							<UButton
								:icon="icons.dislike"
								aria-label="Disliked tracks"
								:color="selectedView() === 'disliked' ? 'primary' : 'neutral'"
								:variant="selectedView() === 'disliked' ? 'soft' : 'ghost'"
								:aria-pressed="selectedView() === 'disliked'"
								@click="chooseView('disliked')"
							>
								<span class="hidden sm:inline">Disliked</span>
							</UButton>
							<UButton
								:icon="icons.folder"
								aria-label="Playlists"
								:color="selectedView() === 'playlists' ? 'primary' : 'neutral'"
								:variant="selectedView() === 'playlists' ? 'soft' : 'ghost'"
								:aria-pressed="selectedView() === 'playlists'"
								@click="chooseView('playlists')"
							>
								<span class="hidden sm:inline">Playlists</span>
							</UButton>
						</div>
					</template>
					<template #right>
						<UButton
							v-if="selectedView() === 'playlists' && !selectedPlaylistId"
							:icon="icons.plus"
							aria-label="New playlist"
							@click="
								playlistName = '';
								createOpen = true;
							"
						>
							<span class="hidden sm:inline">New playlist</span>
						</UButton>
						<PlayerConnection />
					</template>
				</UDashboardNavbar>
			</template>

			<template #body>
				<div class="w-full">
					<div
						class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(26rem,0.8fr)] lg:items-end"
					>
						<div class="min-w-0">
							<UButton
								v-if="selectedPlaylistId"
								label="All playlists"
								:icon="icons.arrowLeft"
								color="neutral"
								variant="ghost"
								class="mb-4 -ml-2"
								@click="chooseView('playlists')"
							/>
							<div class="flex min-w-0 items-center gap-4">
								<LibraryPlaylistCover
									v-if="playlist"
									:artwork-urls="playlist.artwork_urls"
									:name="playlist.name"
									class="size-20"
								/>
								<div class="min-w-0 flex-1">
									<form
										v-if="playlist && renaming"
										class="flex max-w-xl items-center gap-2"
										@submit.prevent="renamePlaylist"
										@keydown.esc.prevent="cancelRename"
									>
										<UInput
											ref="renameInput"
											v-model="playlistName"
											maxlength="100"
											size="xl"
											class="min-w-0 flex-1"
											aria-label="Playlist name"
										/>
										<UButton
											type="submit"
											:icon="icons.check"
											aria-label="Save playlist name"
											:loading="actionPending"
											:disabled="!playlistName.trim()"
										/>
										<UButton
											:icon="icons.close"
											aria-label="Cancel renaming"
											color="neutral"
											variant="ghost"
											@click="cancelRename"
										/>
									</form>
									<div
										v-else-if="playlist"
										class="flex min-w-0 items-center gap-1"
									>
										<button
											v-if="playlist.access === 'owner'"
											type="button"
											class="playlist-name truncate text-left text-2xl font-semibold text-highlighted"
											@click="beginRename()"
										>
											{{ playlist.name }}
										</button>
										<h1
											v-else
											class="truncate text-2xl font-semibold text-highlighted"
										>
											{{ playlist.name }}
										</h1>
										<UButton
											v-if="playlist.access === 'owner'"
											:icon="icons.pencil"
											aria-label="Rename playlist"
											color="neutral"
											variant="ghost"
											class="size-9 shrink-0 justify-center"
											@click="beginRename()"
										/>
									</div>
									<h1
										v-else
										class="truncate text-2xl font-semibold text-highlighted"
									>
										{{
											selectedView() === "playlists"
												? "Your playlists"
												: "Your library"
										}}
									</h1>
									<p class="mt-2 max-w-2xl text-sm text-muted">
										{{
											playlist
												? `${playlist.entry_count} ${playlist.entry_count === 1 ? "track" : "tracks"}, in the order you chose.`
												: selectedView() === "playlists"
													? "Keep your own ordered collections and queue them whenever you want."
													: "Tracks you explicitly liked or disliked."
										}}
									</p>
								</div>
							</div>
						</div>
						<form
							class="flex min-w-0 gap-2"
							role="search"
							@submit.prevent="searchLibrary"
						>
							<UInput
								v-model="search"
								:icon="icons.search"
								:placeholder="
									selectedView() === 'playlists' && !selectedPlaylistId
										? 'Search playlists'
										: 'Search by track or artist'
								"
								aria-label="Search library"
								class="min-w-0 flex-1"
								size="lg"
							/>
							<UButton type="submit" label="Search" :icon="icons.search" size="lg" />
							<UButton
								v-if="selectedQuery()"
								type="button"
								label="Clear"
								color="neutral"
								variant="ghost"
								size="lg"
								@click="clearSearch"
							/>
						</form>
					</div>

					<p v-if="actionError" class="mt-5 text-sm text-warning" role="alert">
						{{ actionError }}
					</p>

					<section
						aria-labelledby="library-results"
						:aria-busy="initialLoading"
						class="mt-10"
					>
						<div
							v-if="playlist"
							class="mb-5 flex flex-wrap items-center justify-between gap-3"
						>
							<div>
								<h2
									id="library-results"
									class="text-lg font-semibold text-highlighted"
								>
									{{
										selectedQuery()
											? `Results for “${selectedQuery()}”`
											: "Tracks"
									}}
								</h2>
								<p class="mt-1 text-xs text-muted">
									{{
										entryResult
											? `${entryResult.total} ${entryResult.total === 1 ? "entry" : "entries"}`
											: ""
									}}
								</p>
							</div>
							<SharedTrackSelection
								:active="selectingEntries"
								:selected-count="selectedEntries.length"
								:available-count="entryResult?.items.length ?? 0"
								select-all-label="Select page"
								allow-select-all
								:disabled="actionPending"
								@start="selectingEntries = true"
								@select-all="
									selectedEntryIds = new Set(
										entryResult?.items.map(({ entry_id }) => entry_id),
									)
								"
								@clear="selectedEntryIds = new Set()"
								@done="finishEntrySelection"
							>
								<template #actions>
									<UButton
										label="Add selected to queue"
										:icon="icons.plus"
										:disabled="!player.canControl || !selectedEntries.length"
										:loading="actionPending"
										@click="queueSelected"
									/>
								</template>
								<template #idle>
									<UDropdownMenu :items="playlistMenu">
										<UButton
											:icon="icons.ellipsis"
											aria-label="Playlist options"
											color="neutral"
											variant="ghost"
											class="size-10 justify-center"
										/>
									</UDropdownMenu>
								</template>
							</SharedTrackSelection>
						</div>

						<Transition name="library-view" mode="out-in">
							<div :key="libraryViewKey">
								<template
									v-if="selectedView() === 'playlists' && !selectedPlaylistId"
								>
									<LibraryPlaylistList
										v-if="initialLoading"
										:items="[]"
										loading
									/>
									<div
										v-else-if="library.playlists.error.value && !playlistResult"
										class="grid min-h-64 place-items-center text-center"
										role="alert"
									>
										<div>
											<UIcon
												:name="icons.warning"
												class="mx-auto size-9 text-warning"
											/>
											<h3 class="mt-4 font-semibold text-highlighted">
												Playlists could not be loaded
											</h3>
											<UButton
												class="mt-5"
												label="Try again"
												:icon="icons.reload"
												color="neutral"
												variant="outline"
												@click="load(true)"
											/>
										</div>
									</div>
									<div
										v-else-if="playlistResult && !playlistResult.items.length"
										class="py-16 text-center"
									>
										<UIcon
											:name="icons.folder"
											class="mx-auto size-9 text-muted"
										/>
										<h3 class="mt-4 font-semibold text-highlighted">
											{{
												selectedQuery()
													? "No matching playlists"
													: "No playlists yet"
											}}
										</h3>
										<p class="mt-2 text-sm text-muted">
											{{
												selectedQuery()
													? "Try another name."
													: "Create one here or from any track action."
											}}
										</p>
									</div>
									<LibraryPlaylistList
										v-else-if="playlistResult"
										:items="playlistResult.items"
										@select="openPlaylist"
										@rename="editPlaylist"
										@duplicate="duplicatePlaylist"
										@delete="confirmDelete"
									/>
								</template>

								<template v-else-if="playlist">
									<LibraryPlaylistEntryList
										v-if="initialLoading"
										:items="[]"
										loading
										:reorderable="canReorderEntries"
									/>
									<div
										v-else-if="library.entries.error.value && !entryResult"
										class="grid min-h-64 place-items-center text-center"
										role="alert"
									>
										<div>
											<UIcon
												:name="icons.warning"
												class="mx-auto size-9 text-warning"
											/>
											<h3 class="mt-4 font-semibold text-highlighted">
												Playlist tracks could not be loaded
											</h3>
											<UButton
												class="mt-5"
												label="Try again"
												:icon="icons.reload"
												color="neutral"
												variant="outline"
												@click="load(true)"
											/>
										</div>
									</div>
									<div
										v-else-if="entryResult && !entryResult.items.length"
										class="py-16 text-center"
									>
										<UIcon
											:name="icons.music"
											class="mx-auto size-9 text-muted"
										/>
										<h3 class="mt-4 font-semibold text-highlighted">
											{{
												selectedQuery()
													? "No matching tracks"
													: "This playlist is empty"
											}}
										</h3>
										<p class="mt-2 text-sm text-muted">
											{{
												selectedQuery()
													? "Try another title or artist."
													: "Add tracks from the Player, Queue or History."
											}}
										</p>
									</div>
									<LibraryPlaylistEntryList
										v-else-if="entryResult"
										:items="entryResult.items"
										:total="entryResult.total"
										:reorderable="canReorderEntries"
										:selectable="selectingEntries"
										:selected-entry-ids="selectedEntryIds"
										:pending="actionPending"
										@toggle="toggleEntry"
										@remove="removeEntry"
										@move="moveEntry"
									/>
								</template>

								<template v-else>
									<div class="mb-4">
										<h2
											id="library-results"
											class="text-lg font-semibold text-highlighted"
										>
											{{
												selectedQuery()
													? `Results for “${selectedQuery()}”`
													: selectedReaction() === "like"
														? "Liked tracks"
														: "Disliked tracks"
											}}
										</h2>
									</div>
									<LibraryTrackList v-if="initialLoading" :items="[]" loading />
									<div
										v-else-if="library.tracks.error.value && !trackResult"
										class="grid min-h-64 place-items-center text-center"
										role="alert"
									>
										<div>
											<UIcon
												:name="icons.warning"
												class="mx-auto size-9 text-warning"
											/>
											<h3 class="mt-4 font-semibold text-highlighted">
												Your library could not be loaded
											</h3>
											<UButton
												class="mt-5"
												label="Try again"
												:icon="icons.reload"
												color="neutral"
												variant="outline"
												@click="load(true)"
											/>
										</div>
									</div>
									<div
										v-else-if="trackResult && !trackResult.items.length"
										class="py-16 text-center"
									>
										<UIcon
											:name="
												selectedReaction() === 'like'
													? icons.like
													: icons.dislike
											"
											class="mx-auto size-9 text-muted"
										/>
										<h3 class="mt-4 font-semibold text-highlighted">
											{{
												selectedQuery()
													? "No matching tracks"
													: selectedReaction() === "like"
														? "Nothing liked yet"
														: "Nothing disliked yet"
											}}
										</h3>
									</div>
									<LibraryTrackList
										v-else-if="trackResult"
										:items="trackResult.items"
										:start-index="
											(trackResult.page - 1) * trackResult.page_size
										"
									/>
								</template>
							</div>
						</Transition>
					</section>
				</div>
			</template>

			<template v-if="initialLoading || activeResult" #footer>
				<div class="shrink-0 border-t border-default bg-default px-4 sm:px-6">
					<SharedPagePagination
						v-if="activeResult"
						v-model:page="currentPage"
						:page-size="activeResult.page_size"
						:total="activeResult.total"
						:disabled="initialLoading"
						@select="selectPage"
					/>
					<div
						v-else
						class="flex min-h-16 items-center justify-between gap-4"
						aria-hidden="true"
					>
						<USkeleton class="h-3 w-36" />
						<div class="flex items-center gap-1">
							<USkeleton v-for="index in 8" :key="index" class="size-8 rounded-md" />
						</div>
					</div>
				</div>
			</template>
		</UDashboardPanel>

		<UModal
			:open="createOpen"
			title="New playlist"
			description="Give this collection a name."
			@update:open="createOpen = $event"
		>
			<template #body
				><form class="space-y-4" @submit.prevent="createPlaylist">
					<UInput
						v-model="playlistName"
						autofocus
						maxlength="100"
						placeholder="Playlist name"
						class="w-full"
					/>
					<div class="flex justify-end gap-2">
						<UButton
							label="Cancel"
							color="neutral"
							variant="ghost"
							@click="createOpen = false"
						/><UButton
							type="submit"
							label="Create"
							:icon="icons.plus"
							:loading="actionPending"
							:disabled="!playlistName.trim()"
						/>
					</div></form
			></template>
		</UModal>
		<UModal
			:open="deleteOpen"
			title="Delete this playlist?"
			description="The playlist disappears, but its tracks stay in your library and history."
			@update:open="deleteOpen = $event"
		>
			<template #body
				><div class="flex justify-end gap-2">
					<UButton
						label="Cancel"
						color="neutral"
						variant="ghost"
						@click="deleteOpen = false"
					/><UButton
						label="Delete playlist"
						:icon="icons.trash"
						color="error"
						:loading="actionPending"
						@click="deletePlaylist"
					/></div
			></template>
		</UModal>
	</div>
</template>

<style scoped>
.playlist-name {
	text-decoration-color: transparent;
	text-underline-offset: 0.2em;
	transition: text-decoration-color 140ms ease-out;
}
.playlist-name:hover,
.playlist-name:focus-visible {
	text-decoration-line: underline;
	text-decoration-color: currentColor;
}
.playlist-name:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 0.25rem;
}
.library-view-enter-active,
.library-view-leave-active {
	transition:
		opacity 160ms ease-out,
		transform 160ms ease-out;
}
.library-view-enter-from {
	opacity: 0;
	transform: translateY(0.25rem);
}
.library-view-leave-to {
	opacity: 0;
	transform: translateY(-0.125rem);
}
@media (prefers-reduced-motion: reduce) {
	.playlist-name,
	.library-view-enter-active,
	.library-view-leave-active {
		transition: none;
	}
	.library-view-enter-from,
	.library-view-leave-to {
		transform: none;
	}
}
</style>
