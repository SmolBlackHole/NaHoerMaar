<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ReactionValue } from "~/core/models/library";

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Library | NaHörMaar" });

const PAGE_SIZE = 25;
const core = useNuxtApp().$backendCore;
const library = core.workflows.library();
const reactions = core.stores.useLibraryStore();
const route = useRoute();
const router = useRouter();
const { icons } = useTheme();
const search = ref("");
const currentPage = ref(1);
const mounted = ref(false);
const result = computed(() => library.tracks.data.value);
const initialLoading = computed(() => library.tracks.loading.value && !result.value);

function queryValue(value: unknown) {
	return typeof value === "string" ? value : "";
}

function selectedReaction(): ReactionValue {
	return queryValue(route.query.view) === "disliked" ? "dislike" : "like";
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
		view?: "liked" | "disliked";
		q?: string;
		page?: number;
	} = {},
) {
	const view = overrides.view ?? (selectedReaction() === "dislike" ? "disliked" : "liked");
	const q = overrides.q ?? selectedQuery();
	const page = overrides.page ?? selectedPage();
	return {
		...(view === "disliked" ? { view } : {}),
		...(q ? { q } : {}),
		...(page > 1 ? { page: String(page) } : {}),
	};
}

async function load(newSnapshot = false) {
	const page = selectedPage();
	currentPage.value = page;
	search.value = selectedQuery();
	const loaded = await library.loadTracks({
		page,
		pageSize: PAGE_SIZE,
		query: selectedQuery(),
		filters: { reaction: selectedReaction() },
		newSnapshot,
	});
	if (!loaded) return;
	reactions.ingest(loaded.items);
	currentPage.value = loaded.page;
	if (loaded.page !== page)
		await router.replace({ path: "/library", query: routeQuery({ page: loaded.page }) });
}

async function chooseView(view: "liked" | "disliked") {
	await router.push({ path: "/library", query: routeQuery({ view, page: 1 }) });
}

async function searchLibrary() {
	await router.push({
		path: "/library",
		query: routeQuery({ q: search.value.trim(), page: 1 }),
	});
}

async function clearSearch() {
	search.value = "";
	await router.push({ path: "/library", query: routeQuery({ q: "", page: 1 }) });
}

async function selectPage(page: number) {
	await router.push({ path: "/library", query: routeQuery({ page }) });
}

watch(
	() => route.fullPath,
	() => {
		if (mounted.value) void load();
	},
);
watch(
	() => reactions.revision,
	() => {
		if (mounted.value) void load(true);
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
				<UDashboardNavbar title="Library">
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
							<div class="flex items-center gap-3">
								<UIcon :name="icons.library" class="size-6 shrink-0 text-primary" />
								<h1 class="text-2xl font-semibold text-highlighted">
									Your library
								</h1>
							</div>
							<p class="mt-2 max-w-2xl text-sm text-muted">
								Tracks you explicitly liked or disliked, separate from
								listening-based favourites.
							</p>
							<p v-if="result" class="mt-3 text-xs tabular-nums text-muted">
								{{ result.total.toLocaleString() }}
								{{ selectedReaction() === "like" ? "liked" : "disliked" }}
								{{ result.total === 1 ? "track" : "tracks" }}
							</p>
							<USkeleton v-else class="mt-3 h-3 w-28" aria-hidden="true" />
						</div>

						<form
							class="flex min-w-0 gap-2"
							role="search"
							@submit.prevent="searchLibrary"
						>
							<UInput
								v-model="search"
								:icon="icons.search"
								placeholder="Search by track or artist"
								aria-label="Search your library by track or artist"
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

					<section
						aria-labelledby="library-results"
						:aria-busy="initialLoading"
						class="mt-10"
					>
						<div class="mb-4 flex flex-wrap items-end justify-between gap-4">
							<div>
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
							<div
								class="inline-flex rounded-lg bg-elevated p-1"
								role="group"
								aria-label="Library collection"
							>
								<UButton
									type="button"
									label="Liked"
									:icon="icons.like"
									:color="selectedReaction() === 'like' ? 'primary' : 'neutral'"
									:variant="selectedReaction() === 'like' ? 'soft' : 'ghost'"
									:aria-pressed="selectedReaction() === 'like'"
									@click="chooseView('liked')"
								/>
								<UButton
									type="button"
									label="Disliked"
									:icon="icons.dislike"
									:color="
										selectedReaction() === 'dislike' ? 'primary' : 'neutral'
									"
									:variant="selectedReaction() === 'dislike' ? 'soft' : 'ghost'"
									:aria-pressed="selectedReaction() === 'dislike'"
									@click="chooseView('disliked')"
								/>
							</div>
						</div>

						<p
							v-if="library.tracks.error.value && result"
							class="mb-4 text-sm text-warning"
							role="alert"
						>
							The current collection could not be refreshed. The previous results are
							still shown.
						</p>
						<LibraryTrackList v-if="initialLoading" :items="[]" loading />
						<div
							v-else-if="library.tracks.error.value && !result"
							class="grid min-h-64 place-items-center text-center"
							role="alert"
						>
							<div class="max-w-sm">
								<UIcon :name="icons.warning" class="mx-auto size-9 text-warning" />
								<h3 class="mt-4 font-semibold text-highlighted">
									Your library could not be loaded
								</h3>
								<p class="mt-2 text-sm text-muted">
									{{ library.tracks.error.value }}
								</p>
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
						<div v-else-if="result && !result.items.length" class="py-16 text-center">
							<UIcon
								:name="selectedReaction() === 'like' ? icons.like : icons.dislike"
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
							<p class="mt-2 text-sm text-muted">
								{{
									selectedQuery()
										? "Try another title or artist."
										: "Use the reaction buttons wherever a track appears."
								}}
							</p>
						</div>
						<LibraryTrackList
							v-else-if="result"
							:items="result.items"
							:start-index="(result.page - 1) * result.page_size"
						/>
					</section>
				</div>
			</template>

			<template v-if="initialLoading || result" #footer>
				<div class="shrink-0 border-t border-default bg-default px-4 sm:px-6">
					<SharedPagePagination
						v-if="result"
						v-model:page="currentPage"
						:page-size="result.page_size"
						:total="result.total"
						:disabled="library.tracks.loading.value"
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
	</div>
</template>
