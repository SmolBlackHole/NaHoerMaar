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
const renameOpen = ref(false);
const deleteOpen = ref(false);
const playlistName = ref("");
const orderDraft = ref<string[]>([]);

const trackResult = computed(() => library.tracks.data.value);
const playlistResult = computed(() => library.playlists.data.value);
const entryResult = computed(() => library.entries.data.value);
const selectedPlaylistId = computed(() => queryValue(route.query.playlist));
const editingOrder = computed(
	() =>
		selectedView() === "playlists" &&
		Boolean(selectedPlaylistId.value) &&
		route.query.edit === "order",
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
	return value === "disliked" || value === "playlists" ? value : "liked";
}
function selectedReaction(): ReactionValue {
	return selectedView() === "disliked" ? "dislike" : "like";
}
function selectedPage() {
	const page = Number(queryValue(route.query.page));
	return Number.isInteger(page) && page > 0 ? page : 1;
}
function selectedQuery() {
	return editingOrder.value ? "" : queryValue(route.query.q).trim();
}
function routeQuery(
	overrides: {
		view?: LibraryView;
		playlist?: string;
		q?: string;
		page?: number;
		edit?: string;
	} = {},
) {
	const view = overrides.view ?? selectedView();
	const playlistId = overrides.playlist ?? selectedPlaylistId.value;
	const q = overrides.q ?? selectedQuery();
	const page = overrides.page ?? selectedPage();
	const edit = overrides.edit ?? queryValue(route.query.edit);
	return {
		...(view !== "liked" ? { view } : {}),
		...(view === "playlists" && playlistId ? { playlist: playlistId } : {}),
		...(q && !edit ? { q } : {}),
		...(page > 1 && !edit ? { page: String(page) } : {}),
		...(view === "playlists" && playlistId && edit ? { edit } : {}),
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
		page: editingOrder.value ? 1 : page,
		pageSize: editingOrder.value ? 100 : PAGE_SIZE,
		query: selectedQuery(),
		newSnapshot,
	});
	if (!loaded) return;
	orderDraft.value = loaded.items.map(({ entry_id }) => entry_id);
	if (!editingOrder.value) await normalizePage(loaded.page, page);
}

async function chooseView(view: LibraryView) {
	await router.push({
		path: "/library",
		query: routeQuery({ view, playlist: "", q: "", page: 1, edit: "" }),
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
			edit: "",
		}),
	});
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
		renameOpen.value = false;
		playlistName.value = "";
		toast.add({ title: "Playlist renamed", color: "success" });
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}
async function duplicatePlaylist() {
	const current = playlist.value;
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
async function queueAll() {
	const current = playlist.value;
	if (!current || !current.entry_count || actionPending.value) return;
	actionPending.value = true;
	try {
		await core.client.library.queuePlaylist(
			current.playlist_id,
			current.revision,
			crypto.randomUUID(),
		);
		toast.add({
			title: "Playlist added to queue",
			description: `${current.entry_count} tracks added.`,
			color: "success",
		});
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}
async function startOrderEdit() {
	await router.push({
		path: "/library",
		query: routeQuery({ q: "", page: 1, edit: "order" }),
	});
}
async function cancelOrderEdit() {
	await router.push({ path: "/library", query: routeQuery({ edit: "", page: 1 }) });
}
async function saveOrder() {
	const current = playlist.value;
	if (!current || actionPending.value) return;
	actionPending.value = true;
	try {
		playlist.value = await core.client.library.replacePlaylistOrder(
			current.playlist_id,
			orderDraft.value,
			current.revision,
		);
		toast.add({ title: "Playlist order saved", color: "success" });
		await cancelOrderEdit();
		await load(true);
	} catch (failure) {
		actionError.value = failureMessage(failure);
	} finally {
		actionPending.value = false;
	}
}

const playlistMenu = computed<DropdownMenuItem[][]>(() => [
	[
		{
			label: "Rename",
			icon: icons.value.type,
			onSelect: () => {
				playlistName.value = playlist.value?.name ?? "";
				renameOpen.value = true;
			},
		},
		{ label: "Duplicate", icon: icons.value.copy, onSelect: duplicatePlaylist },
		{
			label: "Edit order",
			icon: icons.value.drag,
			disabled: !playlist.value?.entry_count || Boolean(selectedQuery()),
			onSelect: startOrderEdit,
		},
	],
	[
		{
			label: "Delete",
			icon: icons.value.trash,
			color: "error",
			onSelect: () => {
				deleteOpen.value = true;
			},
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
								<UIcon
									v-else
									:name="
										selectedView() === 'playlists'
											? icons.folder
											: icons.library
									"
									class="size-6 shrink-0 text-primary"
								/>
								<div class="min-w-0">
									<h1 class="truncate text-2xl font-semibold text-highlighted">
										{{
											playlist?.name ??
											(selectedView() === "playlists"
												? "Your playlists"
												: "Your library")
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
							v-if="!editingOrder"
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
						<div v-else class="flex items-center justify-end gap-2">
							<UButton
								label="Cancel"
								color="neutral"
								variant="ghost"
								@click="cancelOrderEdit"
							/>
							<UButton
								label="Save order"
								:icon="icons.check"
								:loading="actionPending"
								@click="saveOrder"
							/>
						</div>
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
										editingOrder
											? "Edit order"
											: selectedQuery()
												? `Results for “${selectedQuery()}”`
												: "Tracks"
									}}
								</h2>
								<p class="mt-1 text-xs text-muted">
									{{
										editingOrder
											? "Drag tracks or use the arrow buttons, then save once."
											: entryResult
												? `${entryResult.total} ${entryResult.total === 1 ? "entry" : "entries"}`
												: ""
									}}
								</p>
							</div>
							<div v-if="!editingOrder" class="flex items-center gap-2">
								<UButton
									label="Queue all"
									:icon="icons.play"
									:disabled="!player.canControl || !playlist.entry_count"
									:loading="actionPending"
									@click="queueAll"
								/>
								<UDropdownMenu :items="playlistMenu"
									><UButton
										:icon="icons.ellipsis"
										aria-label="Playlist options"
										color="neutral"
										variant="ghost"
										class="size-10 justify-center"
								/></UDropdownMenu>
							</div>
						</div>

						<template v-if="selectedView() === 'playlists' && !selectedPlaylistId">
							<LibraryPlaylistList v-if="initialLoading" :items="[]" loading />
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
								<UIcon :name="icons.folder" class="mx-auto size-9 text-muted" />
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
							/>
						</template>

						<template v-else-if="playlist">
							<LibraryPlaylistEntryList
								v-if="initialLoading"
								:items="[]"
								loading
								:editing="editingOrder"
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
								<UIcon :name="icons.music" class="mx-auto size-9 text-muted" />
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
								:editing="editingOrder"
								:pending="actionPending"
								@remove="removeEntry"
								@order="orderDraft = $event"
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
										selectedReaction() === 'like' ? icons.like : icons.dislike
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
								:start-index="(trackResult.page - 1) * trackResult.page_size"
							/>
						</template>
					</section>
				</div>
			</template>

			<template v-if="!editingOrder && (initialLoading || activeResult)" #footer>
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
			:open="renameOpen"
			title="Rename playlist"
			description="The tracks and order stay the same."
			@update:open="renameOpen = $event"
		>
			<template #body
				><form class="space-y-4" @submit.prevent="renamePlaylist">
					<UInput v-model="playlistName" autofocus maxlength="100" class="w-full" />
					<div class="flex justify-end gap-2">
						<UButton
							label="Cancel"
							color="neutral"
							variant="ghost"
							@click="renameOpen = false"
						/><UButton
							type="submit"
							label="Rename"
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
