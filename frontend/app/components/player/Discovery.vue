<script setup lang="ts">
import {
	musicSource,
	selectedSources,
	reconcileSelection,
	type CatalogTrack,
} from "#shared/catalog";
import { createCatalogClient } from "~/player/catalog";
import { usePlayerStore } from "~/stores/player";
import { useProfileStore } from "~/stores/profile";

const player = usePlayerStore();
const { icons } = useTheme();
const profile = useProfileStore();
const library = createCatalogClient(profile.request);
watch(
	() => profile.status,
	(status) => {
		if (status !== "authenticated") library.dispose();
	},
);
const {
	results,
	query,
	searchSource,
	searching,
	loadingMore,
	nextOffset,
	searchError,
	preview,
	previewUrl,
	previewError,
	previewPending,
	searchUpdate,
	previewUpdate,
	refreshing,
	refreshError,
	searchExpired,
} = library;
const source = ref("");
const inputError = ref("");
const toast = useToast();
const panelOpen = ref(false);
const launcher = ref<HTMLElement>();
const scrollArea = ref<HTMLElement>();
let scrollTop = 0;
const view = ref("search");
const selected = ref(new Set<number>());
const importing = ref(false);
const parsed = computed(() => musicSource(source.value));
const choices = computed(
	() => preview.value?.entries.filter((item) => item.source_url && !item.unavailable) ?? [],
);
const selection = computed(() => selectedSources(preview.value?.entries ?? [], selected.value));
const loadingPlaylist = computed(
	() => !previewError.value && (previewPending.value || preview.value?.state === "loading"),
);

watch(searchSource, () => {
	if (query.value) {
		view.value = "search";
		panelOpen.value = true;
		void library.search(query.value);
	}
});

watch(source, () => {
	inputError.value = "";
});
watch(
	() => preview.value?.state,
	(state) => {
		if (state === "ready") selected.value = new Set(choices.value.map((item) => item.index));
	},
);

function resetScroll() {
	scrollTop = 0;
	if (scrollArea.value) scrollArea.value.scrollTop = 0;
}

async function openPlaylist(url: string) {
	panelOpen.value = true;
	const preserve = previewUrl.value === url && preview.value?.state === "ready";
	if (!preserve) {
		selected.value = new Set();
		resetScroll();
	}
	view.value = "playlist";
	library.clearSearch();
	await library.openPreview(url, preserve);
}

async function submit() {
	const value = parsed.value;
	inputError.value = "";
	if (value.kind === "invalid") {
		inputError.value =
			"Enter a YouTube link or search for a title or artist (up to 200 characters).";
		return;
	}
	if (value.kind === "playlist") {
		await openPlaylist(value.url);
		return;
	}
	if (value.kind === "search") {
		if (view.value !== "search" || value.query !== query.value) resetScroll();
		view.value = "search";
		panelOpen.value = true;
		library.closePreview();
		await library.search(value.query);
		return;
	}
	const submitted = source.value;
	if (await player.add(value.url)) {
		if (source.value === submitted) source.value = "";
		toast.add({ title: "Track added to the queue" });
	} else
		toast.add({
			title: "Could not add the track",
			description: player.error || undefined,
			color: "error",
		});
}

async function addResult(item: CatalogTrack) {
	if (!item.source_url || item.unavailable) return;
	if (await player.add(item.source_url))
		toast.add({ title: "Added to queue", description: item.title || "Track" });
	else
		toast.add({
			title: "Could not add the track",
			description: player.error || undefined,
			color: "error",
		});
}

function toggle(index: number) {
	const next = new Set(selected.value);
	if (next.has(index)) next.delete(index);
	else next.add(index);
	selected.value = next;
}

function selectAll() {
	selected.value =
		selection.value.length === choices.value.length
			? new Set()
			: new Set(choices.value.map((item) => item.index));
}

async function importSelection() {
	if (importing.value || preview.value?.state !== "ready" || !selection.value.length) return;
	importing.value = true;
	try {
		const urls = [...selection.value];
		const importedPreview = preview.value.id;
		const importedSelection = selected.value;
		if (!(await library.validatePreview()) || preview.value?.id !== importedPreview) return;
		if (await player.addMany(urls)) {
			if (preview.value?.id === importedPreview && selected.value === importedSelection)
				selected.value = new Set();
			toast.add({
				title: `${urls.length} ${urls.length === 1 ? "track" : "tracks"} added to the queue`,
			});
			if (preview.value?.id === importedPreview) panelOpen.value = false;
		} else
			toast.add({
				title: "Could not import the selection",
				description: player.error || undefined,
				color: "error",
			});
	} finally {
		importing.value = false;
	}
}

function applyUpdate() {
	if (view.value === "playlist" && previewUpdate.value) {
		selected.value = reconcileSelection(
			preview.value?.entries ?? [],
			previewUpdate.value.entries,
			selected.value,
		);
		library.applyPreviewUpdate();
		toast.add({
			title: "Playlist updated",
			description: "New or unmatched tracks are left unselected.",
		});
	} else library.applySearchUpdate();
}
function restoreFocus() {
	if (!panelOpen.value) launcher.value?.querySelector("input")?.focus({ preventScroll: true });
}
function restoreScroll() {
	if (scrollArea.value) scrollArea.value.scrollTop = scrollTop;
}
watch(panelOpen, (open) => {
	if (!open) scrollTop = scrollArea.value?.scrollTop ?? scrollTop;
	library.setActive(open);
});
onBeforeUnmount(library.dispose);
</script>
<template>
	<section aria-label="Find music" class="discovery">
		<div ref="launcher">
			<PlayerDiscoveryInput
				v-model="source"
				v-model:provider="searchSource"
				:connected="player.connection === 'live'"
				:enabled="player.enabled"
				:error="inputError"
				@submit="submit"
				@playlist="openPlaylist"
			/>
		</div>
		<UButton
			v-if="query || previewUrl"
			label="Back to results"
			color="neutral"
			variant="link"
			class="mt-2 min-h-11 px-0"
			@click="panelOpen = true"
		/>
		<USlideover
			v-model:open="panelOpen"
			:title="view === 'playlist' ? preview?.title || 'Playlist' : 'Find music'"
			:unmount-on-hide="false"
			:close="{ class: 'size-11 justify-center' }"
			:ui="{
				content: 'w-full max-w-none sm:max-w-2xl ring-0 discovery-panel',
				header: 'border-0 px-4 sm:px-6',
				body: 'p-0 sm:p-0 flex min-h-0 flex-col',
				footer: 'border-0 p-4 sm:p-6',
			}"
			@after:enter="restoreScroll"
			@after:leave="restoreFocus"
		>
			<template #title>
				<a
					v-if="view === 'playlist'"
					:href="previewUrl"
					target="_blank"
					rel="noopener noreferrer"
					class="hover:underline"
					>{{ preview?.title || "YouTube playlist" }}</a
				>
				<span v-else>Find music</span>
			</template>
			<template #body>
				<div class="panel-search">
					<PlayerDiscoveryInput
						v-model="source"
						v-model:provider="searchSource"
						:connected="player.connection === 'live'"
						:enabled="player.enabled"
						:error="inputError"
						@submit="submit"
						@playlist="openPlaylist"
					/>
				</div>
				<div class="panel-update" aria-live="polite">
					<UButton
						v-if="view === 'search' ? searchUpdate : previewUpdate"
						label="Show updated results"
						:icon="icons.reload"
						color="neutral"
						variant="ghost"
						:disabled="importing || player.pending || previewPending || loadingMore"
						class="min-h-11"
						@click="applyUpdate"
					/>
					<p v-else-if="refreshError" class="text-sm text-muted">{{ refreshError }}</p>
					<p
						v-else-if="refreshing && (results.length || preview?.state === 'ready')"
						class="text-xs text-muted"
					>
						Checking for updates…
					</p>
				</div>
				<div ref="scrollArea" class="panel-results">
					<div v-if="view === 'search'" :aria-busy="searching || loadingMore">
						<p v-if="query" class="mb-3 text-sm text-muted">
							Results for “{{ query }}”
						</p>
						<p v-if="searching" role="status" class="sr-only">
							Searching
							{{ searchSource === "youtube_music" ? "YouTube Music" : "YouTube" }}…
						</p>
						<PlayerCatalogList
							v-if="searching || results.length"
							:entries="results"
							:loading="searching && !results.length"
							:loading-more="loadingMore"
							:enabled="player.enabled"
							@add="addResult"
						/>
						<p v-else-if="!searchError" class="py-6 text-sm text-muted">
							{{
								query
									? "No tracks found. Try another title or artist."
									: "Search for a title or artist above."
							}}
						</p>
						<div v-if="searchError" role="alert" class="discovery-message">
							<span>{{ searchError }}</span>
							<UButton
								label="Try again"
								color="neutral"
								variant="ghost"
								:disabled="player.connection !== 'live'"
								@click="
									results.length && !searchExpired
										? library.loadMore()
										: library.search(query)
								"
							/>
						</div>
						<div v-if="results.length" class="discovery-footer">
							<p role="status" class="text-xs text-muted tabular-nums">
								{{ results.length }}
								{{ results.length === 1 ? "result" : "results" }}
							</p>
							<UButton
								v-if="nextOffset !== null && !searchError"
								label="Load more"
								:icon="icons.arrowDown"
								:loading="loadingMore"
								:disabled="loadingMore || player.connection !== 'live'"
								color="neutral"
								variant="ghost"
								class="min-h-11"
								@click="library.loadMore()"
							/>
						</div>
					</div>
					<div v-else :aria-busy="loadingPlaylist">
						<div class="playlist-heading">
							<div class="min-w-0">
								<p role="status" class="mt-1 text-xs text-muted">
									{{
										loadingPlaylist
											? `${preview?.entries.length ?? 0} tracks loaded`
											: preview?.state === "cancelled"
												? "Playlist loading cancelled"
												: `${preview?.entries.length ?? 0} tracks`
									}}
								</p>
							</div>
							<UButton
								v-if="loadingPlaylist"
								label="Cancel"
								:disabled="previewPending"
								color="neutral"
								variant="ghost"
								@click="library.cancelPreview()"
							/>
							<UButton
								v-else-if="
									previewError ||
									preview?.state === 'cancelled' ||
									preview?.state === 'failed'
								"
								label="Reload playlist"
								color="neutral"
								variant="ghost"
								@click="openPlaylist(previewUrl)"
							/>
						</div>
						<p
							v-if="previewError || preview?.error"
							role="alert"
							class="my-3 text-sm text-error"
						>
							{{ previewError || preview?.error }}
						</p>
						<p v-if="preview?.truncated" class="my-3 text-sm text-muted">
							Showing the first {{ preview.limit }} entries. The rest of this playlist
							will not be imported.
						</p>

						<PlayerCatalogList
							v-if="preview?.entries.length || loadingPlaylist"
							:entries="preview?.entries ?? []"
							:loading="loadingPlaylist && !preview?.entries.length"
							selectable
							:selected="selected"
							:enabled="
								preview?.state === 'ready' &&
								!importing &&
								!player.pending &&
								!previewPending
							"
							@toggle="toggle"
						/>
						<p v-else-if="preview?.state === 'ready'" class="py-6 text-sm text-muted">
							This playlist has no tracks available.
						</p>
					</div>
				</div>
			</template>
			<template v-if="view === 'playlist' && preview?.state === 'ready'" #footer>
				<div v-if="preview?.state === 'ready' && choices.length" class="playlist-selection">
					<UButton
						:label="selection.length === choices.length ? 'Deselect all' : 'Select all'"
						color="neutral"
						variant="ghost"
						class="min-h-11"
						:disabled="importing || previewPending"
						@click="selectAll"
					/>
					<span class="text-xs text-muted tabular-nums"
						>{{ selection.length }} selected</span
					>
					<UButton
						:label="`Add ${selection.length || ''} ${selection.length === 1 ? 'track' : 'tracks'}`"
						:icon="icons.plus"
						color="primary"
						variant="solid"
						class="ml-auto min-h-11"
						:disabled="
							!player.enabled ||
							!selection.length ||
							!!previewError ||
							previewPending ||
							importing
						"
						:loading="player.pending || importing"
						@click="importSelection"
					/>
				</div>
			</template>
		</USlideover>
	</section>
</template>
<style scoped>
.discovery {
	margin-bottom: 1.75rem;
}
.panel-search {
	padding: 0 1rem 1rem;
	flex-shrink: 0;
}
.panel-update {
	flex-shrink: 0;
}
.panel-update:not(:empty) {
	padding: 0 1rem 0.75rem;
}
.panel-results {
	flex: 1;
	min-height: 0;
	overflow-y: auto;
	overscroll-behavior: contain;
	scrollbar-gutter: stable;
	padding: 0 1rem 1rem;
	container-type: inline-size;
	container-name: discovery-results;
}
.discovery-footer,
.playlist-heading,
.playlist-selection,
.discovery-message {
	display: flex;
	align-items: center;
	gap: 0.75rem;
}
.discovery-footer,
.playlist-heading,
.discovery-message {
	justify-content: space-between;
	padding-block: 1rem;
}
.playlist-selection {
	width: 100%;
	flex-wrap: wrap;
}
.discovery-message {
	font-size: 0.875rem;
}
@media (prefers-reduced-motion: reduce) {
	:global(.discovery-panel) {
		animation: none !important;
		transition: none !important;
	}
}
@media (min-width: 640px) {
	.panel-search {
		padding-inline: 1.5rem;
	}
	.panel-results {
		padding-inline: 1.5rem;
	}
}
</style>
