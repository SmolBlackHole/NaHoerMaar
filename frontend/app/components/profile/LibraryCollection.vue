<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { Playlist } from "~/core/models/library";

type CollectionView = "liked" | "disliked" | "playlists";

const props = defineProps<{
	userId: string;
	displayName: string;
	view: CollectionView;
}>();
const route = useRoute();
const router = useRouter();
const library = useNuxtApp().$backendCore.workflows.library();
const { icons } = useTheme();
const search = ref("");
let criteria = "";

const selectedQuery = computed(() => {
	const value = Array.isArray(route.query.q) ? route.query.q[0] : route.query.q;
	return typeof value === "string" ? value.trim() : "";
});
const currentPage = computed(() => {
	const value = Array.isArray(route.query.page) ? route.query.page[0] : route.query.page;
	const parsed = Number.parseInt(typeof value === "string" ? value : "1", 10);
	return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
});
const trackResult = computed(() => library.profileTracks.data.value);
const playlistResult = computed(() => library.profilePlaylists.data.value);
const activeResult = computed(() =>
	props.view === "playlists" ? playlistResult.value : trackResult.value,
);
const activeState = computed(() =>
	props.view === "playlists" ? library.profilePlaylists : library.profileTracks,
);
const initialLoading = computed(
	() => activeState.value.loading.value && activeResult.value === null,
);
const heading = computed(() => {
	if (props.view === "liked") return `Liked by ${props.displayName}`;
	if (props.view === "disliked") return `Disliked by ${props.displayName}`;
	return `${props.displayName}'s public playlists`;
});
const description = computed(() => {
	if (props.view === "liked") return "Tracks this listener explicitly liked.";
	if (props.view === "disliked") return "Tracks this listener explicitly disliked.";
	return "Public collections this listener owns or helps curate.";
});
const emptyTitle = computed(() => {
	if (selectedQuery.value) return "Nothing matched that search";
	if (props.view === "liked") return "No liked tracks yet";
	if (props.view === "disliked") return "No disliked tracks yet";
	return "No public playlists yet";
});

async function load() {
	const nextCriteria = `${props.userId}:${props.view}:${selectedQuery.value}`;
	const newSnapshot = criteria !== nextCriteria;
	criteria = nextCriteria;
	if (props.view === "playlists") {
		await library.loadProfilePlaylists(props.userId, {
			page: currentPage.value,
			pageSize: 12,
			query: selectedQuery.value || undefined,
			newSnapshot,
		});
		return;
	}
	await library.loadProfileTracks(props.userId, {
		page: currentPage.value,
		pageSize: 20,
		query: selectedQuery.value || undefined,
		reaction: props.view === "liked" ? "like" : "dislike",
		newSnapshot,
	});
}

function queryFor(page: number, query = selectedQuery.value) {
	return {
		...route.query,
		q: query || undefined,
		page: page > 1 ? String(page) : undefined,
	};
}

async function submitSearch() {
	const query = search.value.trim();
	await router.push({ query: queryFor(1, query) });
}

async function selectPage(page: number) {
	await router.push({ query: queryFor(page) });
}

function openPlaylist(playlist: Playlist) {
	return navigateTo({ path: "/library", query: { playlist: playlist.playlist_id } });
}

watch(
	selectedQuery,
	(value) => {
		search.value = value;
	},
	{ immediate: true },
);
watch(
	() => [props.userId, props.view, selectedQuery.value, currentPage.value] as const,
	() => void load(),
	{ immediate: true },
);
onScopeDispose(library.dispose);
</script>

<template>
	<section class="mt-8" :aria-labelledby="`profile-${view}-heading`">
		<div class="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
			<div>
				<h2 :id="`profile-${view}-heading`" class="text-xl font-semibold text-highlighted">
					{{ heading }}
				</h2>
				<p class="mt-1 text-sm text-muted">{{ description }}</p>
			</div>
			<form
				class="flex w-full gap-2 lg:max-w-md"
				role="search"
				@submit.prevent="submitSearch"
			>
				<UInput
					v-model="search"
					:placeholder="
						view === 'playlists' ? 'Search playlists' : 'Search track or artist'
					"
					:icon="icons.search"
					class="min-w-0 flex-1"
				/>
				<UButton type="submit" label="Search" :icon="icons.search" />
			</form>
		</div>

		<div class="mt-7">
			<LibraryPlaylistList
				v-if="view === 'playlists' && initialLoading"
				:items="[]"
				:total="0"
				:manage="false"
				loading
			/>
			<LibraryTrackList
				v-else-if="view !== 'playlists' && initialLoading"
				:items="[]"
				:reaction-controls="false"
				saved-label="Reacted"
				loading
			/>
			<div
				v-else-if="activeState.error.value && !activeResult"
				class="grid min-h-64 place-items-center text-center"
				role="alert"
			>
				<div>
					<UIcon :name="icons.warning" class="mx-auto size-9 text-warning" />
					<h3 class="mt-4 font-semibold text-highlighted">
						This collection could not be loaded
					</h3>
					<UButton
						class="mt-5"
						label="Try again"
						:icon="icons.reload"
						color="neutral"
						variant="outline"
						@click="load"
					/>
				</div>
			</div>
			<div v-else-if="activeResult && !activeResult.items.length" class="py-16 text-center">
				<UIcon
					:name="
						view === 'liked'
							? icons.like
							: view === 'disliked'
								? icons.dislike
								: icons.library
					"
					class="mx-auto size-9 text-muted"
				/>
				<h3 class="mt-4 font-semibold text-highlighted">{{ emptyTitle }}</h3>
			</div>
			<LibraryPlaylistList
				v-else-if="view === 'playlists' && playlistResult"
				:items="playlistResult.items"
				:total="playlistResult.total"
				:manage="false"
				@select="openPlaylist"
			/>
			<LibraryTrackList
				v-else-if="view !== 'playlists' && trackResult"
				:items="trackResult.items"
				:start-index="(trackResult.page - 1) * trackResult.page_size"
				:reaction-controls="false"
				saved-label="Reacted"
			/>
		</div>

		<div
			v-if="initialLoading || activeResult"
			class="sticky bottom-0 z-10 mt-8 border-t border-default bg-default/95 px-1 backdrop-blur"
		>
			<SharedPagePagination
				:page="currentPage"
				:page-size="activeResult?.page_size ?? (view === 'playlists' ? 12 : 20)"
				:total="activeResult?.total ?? 0"
				:disabled="initialLoading"
				:loading="!activeResult"
				@select="selectPage"
			/>
		</div>
	</section>
</template>
