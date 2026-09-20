<script setup lang="ts">
import { musicSource, selectedSources, type CatalogTrack } from "#shared/catalog";
import { createCatalogClient } from "~/player/catalog";
import { usePlayerStore } from "~/stores/player";

const player = usePlayerStore();
const { icons } = useTheme();
const library = createCatalogClient();
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
} = library;
const source = ref("");
const inputError = ref("");
const feedback = ref("");
const view = ref("search");
const selected = ref(new Set<number>());
const parsed = computed(() => musicSource(source.value));
const playlistLink = computed(() => (parsed.value.kind === "video" ? parsed.value.playlist : null));
const submitLabel = computed(() =>
	parsed.value.kind === "video"
		? "Add track"
		: parsed.value.kind === "playlist"
			? "Open playlist"
			: "Search",
);
const choices = computed(
	() => preview.value?.entries.filter((item) => item.source_url && !item.unavailable) ?? [],
);
const selection = computed(() => selectedSources(preview.value?.entries ?? [], selected.value));
const loadingPlaylist = computed(
	() => !previewError.value && (previewPending.value || preview.value?.state === "loading"),
);
const tabs = [
	{ label: "Search", value: "search" },
	{ label: "Playlist", value: "playlist" },
];

watch(searchSource, () => {
	feedback.value = "";
	if (query.value) {
		view.value = "search";
		void library.search(query.value);
	}
});

watch(source, () => {
	inputError.value = "";
	feedback.value = "";
});
watch(
	() => preview.value?.state,
	(state) => {
		if (state === "ready") selected.value = new Set(choices.value.map((item) => item.index));
	},
);

async function openPlaylist(url: string) {
	selected.value = new Set();
	view.value = "playlist";
	feedback.value = "";
	await library.openPreview(url);
}

async function submit() {
	const value = parsed.value;
	inputError.value = "";
	feedback.value = "";
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
		view.value = "search";
		await library.search(value.query);
		return;
	}
	const submitted = source.value;
	if (await player.add(value.url)) {
		if (source.value === submitted) source.value = "";
		feedback.value = "Track added to the queue.";
	}
}

async function addResult(item: CatalogTrack) {
	if (item.source_url && !item.unavailable && (await player.add(item.source_url)))
		feedback.value = `${item.title || "Track"} added to the queue.`;
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
	if (preview.value?.state !== "ready" || !selection.value.length) return;
	const urls = [...selection.value];
	const importedPreview = preview.value.id;
	const importedSelection = selected.value;
	if (!(await library.validatePreview()) || preview.value?.id !== importedPreview) return;
	if (await player.addMany(urls)) {
		if (preview.value?.id === importedPreview && selected.value === importedSelection)
			selected.value = new Set();
		feedback.value = `${urls.length} ${urls.length === 1 ? "track" : "tracks"} added to the queue.`;
	}
}

function closePane() {
	if (view.value === "playlist") {
		library.closePreview();
		selected.value = new Set();
		view.value = "search";
	} else {
		library.clearSearch();
		if (previewUrl.value) view.value = "playlist";
	}
}

onBeforeUnmount(library.dispose);
</script>

<template>
	<section aria-label="Find music" class="discovery">
		<form class="discovery-form" @submit.prevent="submit">
			<div
				class="discovery-search"
				:class="{ 'is-invalid': inputError, 'is-disabled': player.connection !== 'live' }"
			>
				<UIcon :name="icons.search" class="search-icon size-5 shrink-0 text-muted" />
				<label class="sr-only" for="youtube-link">Link, title or artist</label>
				<input
					id="youtube-link"
					v-model="source"
					type="text"
					placeholder="Link, title or artist"
					autocomplete="off"
					:maxlength="2048"
					class="discovery-input"
					:disabled="player.connection !== 'live'"
					:aria-invalid="!!inputError"
					:aria-describedby="inputError ? 'discovery-error' : undefined"
				/>
				<USelect
					v-model="searchSource"
					:items="[
						{ label: 'YouTube Music', value: 'youtube_music' },
						{ label: 'Videos', value: 'youtube' },
					]"
					:disabled="player.connection !== 'live'"
					:trailing-icon="icons.chevronDown"
					:ui="{ content: 'min-w-44', item: 'min-h-11 items-center' }"
					aria-label="Search source"
					variant="none"
					class="search-source"
				>
					<template #default>
						<template v-if="searchSource === 'youtube_music'">
							<span class="source-full">YouTube Music</span>
							<span class="source-short">Music</span>
						</template>
						<span v-else>Videos</span>
					</template>
				</USelect>
			</div>
			<UButton
				type="submit"
				:aria-label="submitLabel"
				:title="submitLabel"
				:icon="
					parsed.kind === 'video'
						? icons.plus
						: parsed.kind === 'playlist'
							? icons.list
							: icons.search
				"
				size="lg"
				color="neutral"
				variant="solid"
				class="discovery-submit"
				:disabled="
					!source.trim() ||
					player.connection !== 'live' ||
					(parsed.kind === 'video' && !player.enabled)
				"
				><span class="discovery-submit-label">{{ submitLabel }}</span></UButton
			>
		</form>
		<p v-if="inputError" id="discovery-error" role="alert" class="mt-2 text-sm text-error">
			{{ inputError }}
		</p>
		<UButton
			v-if="playlistLink"
			:icon="icons.list"
			label="Open playlist instead"
			color="neutral"
			variant="link"
			class="mt-2 px-0"
			:disabled="player.connection !== 'live'"
			@click="openPlaylist(playlistLink)"
		/>
		<div v-if="query || previewUrl" class="discovery-pane">
			<div class="discovery-heading">
				<UTabs
					v-if="previewUrl"
					v-model="view"
					:items="tabs"
					:content="false"
					variant="link"
					:ui="{ indicator: 'transition-none' }"
				/>
				<h3
					v-else
					class="min-w-0 truncate text-sm font-medium text-highlighted"
					:title="query"
				>
					Results for “{{ query }}”
				</h3>
				<UButton
					:icon="icons.close"
					:aria-label="view === 'playlist' ? 'Close playlist' : 'Close search results'"
					color="neutral"
					variant="ghost"
					class="size-10 shrink-0 justify-center"
					@click="closePane"
				/>
			</div>
			<div v-if="view === 'search'" :aria-busy="searching || loadingMore">
				<p v-if="previewUrl && query" class="mb-3 text-sm text-muted">
					Results for “{{ query }}”
				</p>
				<p v-if="searching" role="status" class="sr-only">
					Searching {{ searchSource === "youtube_music" ? "YouTube Music" : "YouTube" }}…
				</p>
				<PlayerCatalogList
					v-if="searching || results.length"
					:entries="results"
					:loading="searching"
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
						@click="results.length ? library.loadMore() : library.search(query)"
					/>
				</div>
				<div v-if="results.length" class="discovery-footer">
					<p role="status" class="text-xs text-muted tabular-nums">
						{{ results.length }} {{ results.length === 1 ? "result" : "results" }}
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
						<a
							:href="previewUrl"
							target="_blank"
							rel="noopener noreferrer"
							class="block truncate font-medium text-highlighted hover:underline"
							>{{ preview?.title || "YouTube playlist" }}</a
						>
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
					Showing the first {{ preview.limit }} entries. The rest of this playlist will
					not be imported.
				</p>
				<div v-if="preview?.state === 'ready' && choices.length" class="playlist-selection">
					<UButton
						:label="selection.length === choices.length ? 'Deselect all' : 'Select all'"
						color="neutral"
						variant="ghost"
						@click="selectAll"
					/>
					<span class="text-xs text-muted tabular-nums"
						>{{ selection.length }} selected</span
					>
					<UButton
						:label="`Add ${selection.length || ''} ${selection.length === 1 ? 'track' : 'tracks'}`"
						:icon="icons.plus"
						color="primary"
						variant="soft"
						class="ml-auto"
						:disabled="!player.enabled || !selection.length || !!previewError"
						:loading="player.pending"
						@click="importSelection"
					/>
				</div>
				<PlayerCatalogList
					v-if="preview?.entries.length"
					:entries="preview.entries"
					selectable
					:selected="selected"
					:enabled="preview.state === 'ready' && !player.pending"
					@toggle="toggle"
				/>
				<p v-else-if="preview?.state === 'ready'" class="py-6 text-sm text-muted">
					This playlist has no tracks available.
				</p>
			</div>
		</div>
		<p v-if="feedback" role="status" class="mt-3 text-xs text-muted">{{ feedback }}</p>
	</section>
</template>

<style scoped>
.discovery {
	margin-bottom: 1.75rem;
}
.discovery-search {
	display: flex;
	align-items: center;
	flex: 1;
	min-width: 0;
	min-height: 3rem;
	border: 1px solid var(--ui-border-accented);
	border-radius: 0.5rem;
	background: var(--ui-bg-elevated);
}
.discovery-search:has(.discovery-input:focus-visible) {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
.discovery-search.is-invalid {
	border-color: var(--ui-error);
}
.discovery-search.is-disabled {
	opacity: 0.5;
}
.search-icon {
	margin-left: 0.875rem;
}
.discovery-input {
	flex: 1;
	min-width: 0;
	width: 100%;
	min-height: 2.75rem;
	padding: 0.5rem 0.75rem;
	background: transparent;
	color: var(--ui-text-highlighted);
	font-size: 1rem;
	outline: none;
	caret-color: var(--ui-primary);
}
.discovery-input::placeholder {
	color: var(--ui-text-muted);
}
.search-source {
	flex-shrink: 0;
	margin-right: 0.25rem;
	min-height: 2.75rem;
	padding: 0.5rem 2rem 0.5rem 0.75rem;
	background: transparent;
	font-size: 0.8125rem;
	color: var(--ui-text);
	cursor: pointer;
}
.search-source:hover {
	background: var(--ui-bg-accented);
}
.search-source:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: -3px;
}
.source-short {
	display: none;
}
.discovery-form {
	display: flex;
	gap: 0.5rem;
}
.discovery-submit {
	flex-shrink: 0;
	justify-content: center;
	min-width: 7.5rem;
	min-height: 3rem;
}
.discovery-pane {
	margin-top: 1.5rem;
	padding-bottom: 0.5rem;
	border-bottom: 1px solid var(--ui-border);
}
.discovery-heading,
.discovery-footer,
.playlist-heading,
.playlist-selection,
.discovery-message {
	display: flex;
	align-items: center;
	gap: 0.75rem;
}
.discovery-heading {
	justify-content: space-between;
	margin-bottom: 0.5rem;
}
.discovery-footer {
	justify-content: space-between;
	min-height: 3.5rem;
	padding-block: 0.25rem;
}
.playlist-heading {
	justify-content: space-between;
	padding-block: 0.75rem;
}
.playlist-selection {
	flex-wrap: wrap;
	padding-block: 0.5rem;
}
.discovery-message {
	justify-content: space-between;
	padding-block: 1rem;
	font-size: 0.875rem;
	color: var(--ui-text);
}
@container workspace (max-width: 600px) {
	.search-icon {
		display: none;
	}
	.discovery-submit {
		min-width: 3rem;
		width: 3rem;
		padding-inline: 0;
	}
	.discovery-submit-label {
		display: none;
	}
	.source-full {
		display: none;
	}
	.source-short {
		display: inline;
	}
}
</style>
