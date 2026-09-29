<script setup lang="ts">
import type { PlaybackHistoryEntry } from "~/core/models/playbacks";
definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "History | NaHörMaar" });

const PAGE_SIZE = 25;
const core = useNuxtApp().$backendCore;
const playbackHistory = core.workflows.playbackHistory();
const { icons } = useTheme();
const search = ref("");
const activeQuery = ref("");
const radioMode = ref<"all" | "only" | "exclude">("all");
const requestedBy = ref("all");
const currentPage = ref(1);
const selectedEntries = ref<PlaybackHistoryEntry[]>([]);
const selectedPlaybackIds = computed(
	() => new Set(selectedEntries.value.map(({ playback_id }) => playback_id)),
);
const result = computed(() => playbackHistory.history.data.value);
const initialLoading = computed(() => playbackHistory.history.loading.value && !result.value);
const radioItems = [
	{ label: "All plays", value: "all" },
	{ label: "Only Radio", value: "only" },
	{ label: "Without Radio", value: "exclude" },
];
const requesterItems = computed(() => [
	{ label: "Everyone", value: "all" },
	...(result.value?.contributors ?? []).map((contributor) => ({
		label: contributor.display_name,
		value: contributor.user_id,
	})),
]);
const hasFilters = computed(() => radioMode.value !== "all" || requestedBy.value !== "all");

async function load(page: number, newSnapshot = false) {
	const loaded = await playbackHistory.load({
		page,
		pageSize: PAGE_SIZE,
		query: activeQuery.value,
		filters: {
			radio: radioMode.value === "all" ? undefined : radioMode.value === "only",
			requestedBy: requestedBy.value === "all" ? undefined : requestedBy.value,
		},
		newSnapshot,
	});
	if (loaded) currentPage.value = loaded.page;
	return loaded;
}

function searchHistory() {
	clearSelection();
	activeQuery.value = search.value.trim();
	playbackHistory.history.set(null);
	void load(1, true);
}

function clearSearch() {
	clearSelection();
	search.value = "";
	activeQuery.value = "";
	playbackHistory.history.set(null);
	void load(1, true);
}

async function changePage(page: number) {
	const previousPage = result.value?.page ?? 1;
	if (!(await load(page))) currentPage.value = previousPage;
}

function resetFilters() {
	radioMode.value = "all";
	requestedBy.value = "all";
}

function toggleSelection(entry: PlaybackHistoryEntry) {
	const selected = selectedPlaybackIds.value.has(entry.playback_id);
	selectedEntries.value = selected
		? selectedEntries.value.filter(({ playback_id }) => playback_id !== entry.playback_id)
		: [...selectedEntries.value, entry];
}

function clearSelection() {
	selectedEntries.value = [];
}

watch([radioMode, requestedBy], () => {
	clearSelection();
	void load(1, true);
});

onMounted(() => void load(1, true));
onScopeDispose(playbackHistory.dispose);
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1">
		<UDashboardPanel
			id="history"
			class="min-h-0 min-w-0 flex-1"
			:ui="{ body: 'pt-6 sm:pt-6 pb-5 sm:pb-5' }"
		>
			<template #header>
				<UDashboardNavbar title="History">
					<template #leading><UDashboardSidebarCollapse /></template>
					<template #right><PlayerConnection /></template>
				</UDashboardNavbar>
			</template>

			<template #body>
				<div class="w-full">
					<div
						class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(26rem,0.8fr)] lg:items-end"
					>
						<div>
							<h1 class="text-2xl font-semibold text-highlighted">
								Playback history
							</h1>
							<p class="mt-2 max-w-2xl text-sm text-muted">
								Every confirmed start, including repeat plays and Radio picks.
								Nothing gets folded away.
							</p>
							<p v-if="result" class="mt-3 text-xs tabular-nums text-muted">
								{{ result.total.toLocaleString() }}
								{{ result.total === 1 ? "play" : "plays" }} in this view
							</p>
							<USkeleton v-else class="mt-3 h-3 w-28" aria-hidden="true" />
						</div>

						<form
							class="flex min-w-0 gap-2"
							role="search"
							@submit.prevent="searchHistory"
						>
							<UInput
								v-model="search"
								:icon="icons.search"
								placeholder="Search by track or artist"
								aria-label="Search playback history by track or artist"
								class="min-w-0 flex-1"
								size="lg"
							/>
							<UButton type="submit" label="Search" :icon="icons.search" size="lg" />
							<UButton
								v-if="activeQuery"
								type="button"
								label="Clear"
								color="neutral"
								variant="ghost"
								size="lg"
								@click="clearSearch"
							/>
						</form>
					</div>

					<section
						aria-labelledby="history-results"
						:aria-busy="initialLoading"
						class="mt-10"
					>
						<div class="mb-4 flex flex-wrap items-end justify-between gap-4">
							<div>
								<h2
									id="history-results"
									class="text-lg font-semibold text-highlighted"
								>
									{{
										activeQuery
											? "Results for “" + activeQuery + "”"
											: hasFilters
												? "Filtered plays"
												: "All plays"
									}}
								</h2>
								<p v-if="result" class="mt-1 text-xs tabular-nums text-muted">
									Showing
									{{
										result.total ? (result.page - 1) * result.page_size + 1 : 0
									}}-{{
										Math.min(result.page * result.page_size, result.total)
									}}
									of {{ result.total.toLocaleString() }}
								</p>
								<USkeleton v-else class="mt-2 h-3 w-32" aria-hidden="true" />
							</div>

							<div class="history-filters">
								<label class="grid gap-1">
									<span class="text-xs font-medium text-muted">Radio</span>
									<USelect
										v-model="radioMode"
										:items="radioItems"
										value-key="value"
										aria-label="Filter playback history by Radio"
										class="history-filter-select"
										:disabled="initialLoading"
									/>
								</label>
								<label class="grid gap-1">
									<span class="text-xs font-medium text-muted">Requested by</span>
									<USelect
										v-model="requestedBy"
										:items="requesterItems"
										value-key="value"
										aria-label="Filter playback history by requester"
										class="history-filter-select"
										:disabled="initialLoading"
									/>
								</label>
								<UButton
									v-if="hasFilters"
									label="Reset"
									color="neutral"
									variant="ghost"
									class="history-filter-reset mb-px"
									@click="resetFilters"
								/>
							</div>
						</div>

						<p
							v-if="playbackHistory.history.error.value && result"
							class="mb-4 text-sm text-warning"
							role="alert"
						>
							The requested page could not be loaded. Try again.
						</p>

						<div
							v-if="selectedEntries.length"
							class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-elevated px-4 py-3"
							role="status"
						>
							<div>
								<p class="text-sm font-medium text-highlighted">
									{{ selectedEntries.length }} selected
								</p>
								<p class="mt-1 text-xs text-muted">
									Selection stays while you move through these results.
								</p>
							</div>
							<div class="flex items-center gap-2">
								<UButton
									label="Clear"
									color="neutral"
									variant="ghost"
									@click="clearSelection"
								/>
								<LibraryPlaylistPicker
									:tracks="
										selectedEntries.map((entry) => ({
											track_id: entry.track_id,
											preferred_source_id: entry.source_id,
										}))
									"
									label="Add selected to playlist"
									@added="clearSelection"
								/>
							</div>
						</div>

						<PlayerRecentList
							v-if="playbackHistory.history.loading.value && !result"
							:entries="[]"
							:skeleton-count="8"
							layout="history"
							selectable
							loading
						/>

						<div
							v-else-if="playbackHistory.history.error.value && !result"
							class="grid min-h-64 place-items-center text-center"
							role="alert"
						>
							<div class="max-w-sm">
								<UIcon :name="icons.warning" class="mx-auto size-9 text-warning" />
								<h3 class="mt-4 font-semibold text-highlighted">
									History could not be loaded
								</h3>
								<p class="mt-2 text-sm text-muted">
									{{ playbackHistory.history.error.value }}
								</p>
								<UButton
									class="mt-5"
									label="Try again"
									:icon="icons.reload"
									color="neutral"
									variant="outline"
									@click="load(currentPage, true)"
								/>
							</div>
						</div>

						<div v-else-if="result && !result.items.length" class="py-16 text-center">
							<UIcon :name="icons.clock" class="mx-auto size-9 text-muted" />
							<h3 class="mt-4 font-semibold text-highlighted">
								{{
									activeQuery || hasFilters
										? "No matching plays"
										: "Nothing has played yet"
								}}
							</h3>
							<p class="mt-2 text-sm text-muted">
								{{
									activeQuery || hasFilters
										? "Try another title, artist or filter."
										: "Confirmed playback starts will appear here."
								}}
							</p>
						</div>

						<PlayerRecentList
							v-else-if="result"
							:entries="result.items"
							layout="history"
							selectable
							:selected-playback-ids="selectedPlaybackIds"
							@toggle="toggleSelection"
						/>
					</section>
				</div>
			</template>

			<template v-if="initialLoading || (result && result.page_count > 1)" #footer>
				<div class="shrink-0 border-t border-default bg-default px-4 sm:px-6">
					<SharedPagePagination
						v-if="result"
						v-model:page="currentPage"
						:page-size="result.page_size"
						:total="result.total"
						:disabled="playbackHistory.history.loading.value"
						@select="changePage"
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
	</div>
</template>

<style scoped>
.history-filters {
	display: flex;
	flex-wrap: wrap;
	align-items: end;
	gap: 0.5rem;
}
.history-filter-select {
	width: 10rem;
}
.history-filters label:nth-child(2) .history-filter-select {
	width: 12rem;
}
@container workspace (max-width: 600px) {
	.history-filters {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		width: 100%;
	}
	.history-filters label,
	.history-filter-select {
		width: 100% !important;
		min-width: 0;
	}
	.history-filter-reset {
		grid-column: 1 / -1;
		justify-self: start;
	}
}
</style>
