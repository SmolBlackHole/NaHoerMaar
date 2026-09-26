// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { computed, onScopeDispose, ref, shallowRef, watch } from "vue";
import { musicSource, type DiscoveryEntry, type SearchProvider } from "../core/models/catalog";
import type { PlayerCommands, PlayerState } from "../core/models/player";

type PanelView = "search" | "playlist" | "radio";

function queuePresence(trackId: string, state: PlayerState | null): string | null {
	if (state?.runtime.current?.track.id === trackId) return "Now playing";
	return state?.queue.some(({ request }) => request.track.id === trackId)
		? "Already queued"
		: null;
}

export function useBackendDiscovery() {
	const core = useNuxtApp().$backendCore;
	const session = core.stores.useSessionStore();
	const player = core.stores.usePlayerStore();
	const catalog = core.workflows.discovery();
	const source = ref("");
	const provider = ref<SearchProvider>("youtube_music");
	const inputError = ref("");
	const panelOpen = ref(false);
	const view = ref<PanelView>("search");
	const query = ref("");
	const playlistUrl = ref("");
	const selected = ref(new Set<number>());
	const skipDuplicates = ref(false);
	const importing = ref(false);
	const submitting = ref(false);
	const loadingMore = ref(false);
	const pendingTrackIds = ref(new Set<string>());
	const radioSeed = shallowRef<PlayerCommands["startRadio"]["seed"] | null>(null);
	const radioTitle = ref("");

	const page = computed(() => catalog.results.data.value);
	const entries = computed(() => page.value?.entries ?? []);
	const availableEntries = computed(() =>
		entries.value.filter(({ source }) => source.availability !== "unavailable"),
	);
	const selectedEntries = computed(() =>
		availableEntries.value.filter(({ position }) => selected.value.has(position)),
	);
	const duplicateCount = computed(() =>
		skipDuplicates.value
			? selectedEntries.value.filter(({ track }) => queuePresence(track.id, player.state))
					.length
			: 0,
	);
	const addCount = computed(() => selectedEntries.value.length - duplicateCount.value);
	const canSearch = computed(() => session.status === "authenticated");
	const canControl = computed(() => player.canControl);
	const loading = computed(() => catalog.results.loading.value);
	const error = computed(
		() => catalog.results.error.value ?? catalog.linkedTrack.error.value ?? player.error,
	);
	const parsed = computed(() => musicSource(source.value));

	function resetSelection(value = entries.value) {
		selected.value = new Set(
			value
				.filter(({ source }) => source.availability !== "unavailable")
				.map(({ position }) => position),
		);
	}

	async function search(text: string, refresh = false) {
		query.value = text;
		view.value = "search";
		panelOpen.value = true;
		playlistUrl.value = "";
		radioSeed.value = null;
		if (!refresh) catalog.results.set(null);
		await catalog.search(text, { provider: provider.value, refresh });
	}

	async function openPlaylist(url: string, refresh = false) {
		const selectedTrackIds = new Set(selectedEntries.value.map(({ track }) => track.id));
		playlistUrl.value = url;
		view.value = "playlist";
		panelOpen.value = true;
		query.value = "";
		radioSeed.value = null;
		if (!refresh) {
			catalog.results.set(null);
			selected.value = new Set();
		}
		const result = await catalog.playlist(url, {
			limit: 100,
			provider: provider.value,
			refresh,
		});
		if (!result) return;
		if (!refresh) resetSelection(result.entries);
		else
			selected.value = new Set(
				result.entries
					.filter(
						({ source: trackSource, track }) =>
							trackSource.availability !== "unavailable" &&
							selectedTrackIds.has(track.id),
					)
					.map(({ position }) => position),
			);
	}

	async function refresh() {
		if (page.value?.kind === "playlist" && playlistUrl.value)
			await openPlaylist(playlistUrl.value, true);
		else if (query.value) await search(query.value, true);
	}

	async function more() {
		if (loadingMore.value || page.value?.next_offset === null) return;
		loadingMore.value = true;
		try {
			await catalog.more();
		} finally {
			loadingMore.value = false;
		}
	}

	async function addEntries(items: readonly DiscoveryEntry[], skip: boolean) {
		if (!items.length || !player.canControl) return false;
		const ids = new Set(items.map(({ track }) => track.id));
		pendingTrackIds.value = new Set([...pendingTrackIds.value, ...ids]);
		try {
			const result = await player.add(
				items.map(({ source: trackSource, track }) => ({
					track_id: track.id,
					source_id: trackSource.id,
				})),
				skip,
			);
			return result !== null;
		} finally {
			pendingTrackIds.value = new Set(
				[...pendingTrackIds.value].filter((id) => !ids.has(id)),
			);
		}
	}

	async function addResult(entry: DiscoveryEntry) {
		await addEntries([entry], false);
	}

	async function addVideo(url: string) {
		submitting.value = true;
		try {
			const track = await catalog.resolveLink(url);
			if (!track) return false;
			const trackSource =
				track.sources.find((candidate) => candidate.source_url === url) ?? track.sources[0];
			if (!trackSource) {
				inputError.value = "No playable source was found for this track.";
				return false;
			}
			return await addEntries([{ position: 0, source: trackSource, track }], false);
		} finally {
			submitting.value = false;
		}
	}

	async function submit() {
		inputError.value = "";
		const value = parsed.value;
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
			await search(value.query);
			return;
		}
		if (await addVideo(value.url)) source.value = "";
	}

	function toggle(position: number) {
		const next = new Set(selected.value);
		if (next.has(position)) next.delete(position);
		else next.add(position);
		selected.value = next;
	}

	function selectAll() {
		selected.value =
			selectedEntries.value.length === availableEntries.value.length
				? new Set()
				: new Set(availableEntries.value.map(({ position }) => position));
	}

	async function importSelection() {
		if (importing.value || !selectedEntries.value.length) return;
		importing.value = true;
		try {
			if (await addEntries(selectedEntries.value, skipDuplicates.value)) {
				selected.value = new Set();
				panelOpen.value = false;
			}
		} finally {
			importing.value = false;
		}
	}

	function openTrackRadio(entry: DiscoveryEntry) {
		radioSeed.value = {
			kind: "track",
			track_source_id: entry.source.id,
			discovery_snapshot_id: null,
		};
		radioTitle.value = entry.track.title;
		view.value = "radio";
		panelOpen.value = true;
	}

	function openPlaylistRadio() {
		if (!page.value || page.value.kind !== "playlist") return;
		radioSeed.value = {
			kind: "playlist",
			discovery_snapshot_id: page.value.version,
			track_source_id: null,
		};
		radioTitle.value = page.value.playlist_title ?? "Playlist";
		view.value = "radio";
		panelOpen.value = true;
	}

	async function startRadio() {
		const seed = radioSeed.value;
		if (!seed || !player.canControl) return;
		const result = await player.startRadio(seed);
		if (result) panelOpen.value = false;
		return result;
	}

	function backFromRadio() {
		view.value = page.value?.kind === "playlist" ? "playlist" : "search";
		radioSeed.value = null;
	}

	watch(provider, () => {
		if (query.value) void search(query.value);
	});
	watch(source, () => {
		inputError.value = "";
	});
	watch(
		() => [session.status, session.generation] as const,
		([status]) => {
			if (status === "authenticated") return;
			panelOpen.value = false;
			catalog.results.set(null);
			selected.value = new Set();
			source.value = "";
		},
		{ flush: "sync" },
	);

	onScopeDispose(() => catalog.dispose());

	return {
		player,
		source,
		provider,
		inputError,
		panelOpen,
		view,
		query,
		playlistUrl,
		selected,
		skipDuplicates,
		importing,
		submitting,
		loadingMore,
		pendingTrackIds,
		radioTitle,
		page,
		entries,
		availableEntries,
		selectedEntries,
		duplicateCount,
		addCount,
		canSearch,
		canControl,
		loading,
		error,
		parsed,
		search,
		openPlaylist,
		refresh,
		more,
		submit,
		addResult,
		toggle,
		selectAll,
		importSelection,
		openTrackRadio,
		openPlaylistRadio,
		startRadio,
		backFromRadio,
		queuePresence: (trackId: string) => queuePresence(trackId, player.state),
	};
}
