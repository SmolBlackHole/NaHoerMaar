import { ref, shallowRef, onScopeDispose } from "vue";
import { ApiFailure } from "../repositories/transport";
import { useRepositories } from "../repositories";
import {
	catalogPage,
	type CatalogTrack,
	type CatalogPage,
	type SearchSource,
} from "../../shared/catalog";
import type { DiscoveryPage } from "../../shared/engine";

export function useCatalog() {
	const { catalog: api } = useRepositories();
	const results = shallowRef<CatalogTrack[]>([]);
	const query = ref("");
	const searchSource = ref<SearchSource>("youtube_music");
	const searching = ref(false);
	const loadingMore = ref(false);
	const nextOffset = ref<number | null>(null);
	const searchError = ref("");
	const searchExpired = ref(false);
	const searchSnapshot = ref<string | null>(null);
	const searchUpdate = shallowRef<CatalogPage | null>(null);
	const previewUpdate = shallowRef<CatalogPage | null>(null);
	const refreshing = ref(false);
	const refreshError = ref("");
	let searchedSource: SearchSource = "youtube_music";
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	let active = true;
	const preview = shallowRef<CatalogPage | null>(null);
	const previewUrl = ref("");
	const previewError = ref("");
	const previewPending = ref(false);
	let searchAbort: AbortController | undefined;
	let previewVersion = 0;
	let previewAbort: AbortController | undefined;
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function search(text: string) {
		const same = query.value === text && searchedSource === searchSource.value;
		searchAbort?.abort();
		clearTimeout(searchTimer);
		query.value = text;
		searchedSource = searchSource.value;
		searchExpired.value = false;
		searchUpdate.value = null;
		refreshError.value = "";
		if (!same) {
			results.value = [];
			searchSnapshot.value = null;
			nextOffset.value = null;
		}
		loadingMore.value = false;
		await searchPage(0, same);
	}

	async function loadMore() {
		if (
			searching.value ||
			loadingMore.value ||
			nextOffset.value === null ||
			searchExpired.value
		)
			return;
		await searchPage(nextOffset.value);
	}

	function append(page: CatalogPage) {
		const seen = new Set(results.value.map((item) => item.track_id));
		results.value = [
			...results.value,
			...page.entries.filter((item) => {
				if (!item.track_id) return true;
				if (seen.has(item.track_id)) return false;
				seen.add(item.track_id);
				return true;
			}),
		];
		nextOffset.value = page.next_offset;
		searchSnapshot.value = page.version;
	}

	function observe(page: CatalogPage, abort: AbortController) {
		refreshing.value = page.refresh.refreshing;
		refreshError.value = page.refresh.error || "";
		if (active && (page.refresh.refreshing || page.refresh.latest_version !== page.version)) {
			clearTimeout(searchTimer);
			searchTimer = setTimeout(() => void checkSearch(abort), 1000);
		}
	}

	async function searchPage(offset: number, preserve = false) {
		searchAbort?.abort();
		clearTimeout(searchTimer);
		const abort = new AbortController();
		searchAbort = abort;
		searchError.value = "";
		const busy = offset === 0 ? searching : loadingMore;
		busy.value = true;
		try {
			const found = catalogPage(
				offset && searchSnapshot.value
					? await api.snapshot("search", searchSnapshot.value, offset, 20, abort.signal)
					: await api.search(query.value, searchSource.value, abort.signal),
			);
			if (abort.signal.aborted) return;
			if (preserve && results.value.length) {
				if (found.version !== searchSnapshot.value) searchUpdate.value = found;
			} else {
				if (!offset) results.value = [];
				append(found);
			}
			observe(found, abort);
		} catch (error) {
			if (!abort.signal.aborted) {
				searchExpired.value =
					error instanceof ApiFailure && [404, 410].includes(error.status);
				searchError.value =
					error instanceof Error ? error.message : "Search failed. Try again.";
			}
		} finally {
			if (searchAbort === abort) busy.value = false;
		}
	}

	async function checkSearch(abort: AbortController) {
		if (!active || abort.signal.aborted || !query.value || !searchSnapshot.value) return;
		try {
			const current = catalogPage(
				await api.snapshot("search", searchSnapshot.value, 0, 20, abort.signal),
			);
			if (abort.signal.aborted) return;
			refreshing.value = current.refresh.refreshing;
			refreshError.value = current.refresh.error || "";
			if (current.refresh.latest_version !== searchSnapshot.value) {
				const next = catalogPage(
					await api.snapshot(
						"search",
						current.refresh.latest_version,
						0,
						20,
						abort.signal,
					),
				);
				if (abort.signal.aborted) return;
				searchUpdate.value = next;
			}
			if (current.refresh.refreshing && active)
				searchTimer = setTimeout(() => void checkSearch(abort), 1000);
		} catch (error) {
			if (abort.signal.aborted) return;
			searchExpired.value = error instanceof ApiFailure && [404, 410].includes(error.status);
			refreshError.value =
				error instanceof Error ? error.message : "Could not refresh results.";
		}
	}

	function applySearchUpdate() {
		if (!searchUpdate.value) return;
		searchAbort?.abort();
		clearTimeout(searchTimer);
		results.value = [];
		append(searchUpdate.value);
		searchUpdate.value = null;
		searchError.value = "";
		searchExpired.value = false;
		searching.value = loadingMore.value = false;
	}

	function clearSearch() {
		searchAbort?.abort();
		clearTimeout(searchTimer);
		searching.value = loadingMore.value = false;
		nextOffset.value = null;
		results.value = [];
		query.value = "";
		searchError.value = "";
		searchSnapshot.value = null;
		searchUpdate.value = null;
		searchExpired.value = false;
	}

	function closePreview() {
		previewVersion++;
		previewAbort?.abort();
		clearTimeout(timer);
		preview.value = null;
		previewUpdate.value = null;
		previewPending.value = false;
		previewUrl.value = "";
		previewError.value = "";
	}

	async function readPlaylist(page: DiscoveryPage, abort: AbortController): Promise<CatalogPage> {
		// A snapshot contains at most 100 occurrences, including duplicates.
		const all =
			page.next_offset !== null
				? await api.snapshot("playlist", page.version, 0, 100, abort.signal)
				: page;
		return catalogPage(all);
	}
	function receivePreview(value: CatalogPage) {
		refreshing.value = value.refresh.refreshing;
		refreshError.value = value.refresh.error || "";
		if (preview.value && value.version !== preview.value.version) previewUpdate.value = value;
		else preview.value = value;
	}
	function applyPreviewUpdate() {
		if (previewUpdate.value) preview.value = previewUpdate.value;
		previewUpdate.value = null;
	}
	async function poll(abort: AbortController, version: number) {
		if (!active || abort.signal.aborted || !preview.value) return;
		try {
			const current = await api.snapshot(
				"playlist",
				preview.value.version,
				0,
				20,
				abort.signal,
			);
			if (version !== previewVersion) return;
			refreshing.value = current.refresh.refreshing;
			refreshError.value = current.refresh.error || "";
			if (current.refresh.latest_version !== preview.value.version) {
				const next = await api.snapshot(
					"playlist",
					current.refresh.latest_version,
					0,
					100,
					abort.signal,
				);
				const value = await readPlaylist(next, abort);
				if (version !== previewVersion) return;
				receivePreview(value);
			}
			if (current.refresh.refreshing && active)
				timer = setTimeout(() => void poll(abort, version), 1000);
		} catch (reason) {
			if (version === previewVersion && !abort.signal.aborted)
				refreshError.value =
					reason instanceof Error ? reason.message : "Could not refresh the playlist.";
		}
	}
	async function openPreview(url: string, preserve = false) {
		const previous = preserve ? preview.value : null;
		closePreview();
		preview.value = previous;
		const version = previewVersion;
		const abort = new AbortController();
		previewAbort = abort;
		previewUrl.value = url;
		previewPending.value = true;
		try {
			const page = await api.openPlaylist(url, true, abort.signal);
			if (version !== previewVersion) return;
			const value = await readPlaylist(page, abort);
			if (version !== previewVersion) return;
			receivePreview(value);
			if (active && (page.refresh.refreshing || page.refresh.latest_version !== page.version))
				timer = setTimeout(() => void poll(abort, version), 1000);
		} catch (reason) {
			if (version === previewVersion && !abort.signal.aborted)
				previewError.value =
					reason instanceof Error ? reason.message : "Could not open the playlist.";
		} finally {
			if (version === previewVersion) previewPending.value = false;
		}
	}
	function setActive(value: boolean) {
		if (active === value) return;
		active = value;
		clearTimeout(searchTimer);
		clearTimeout(timer);
		if (!active) {
			searchAbort?.abort();
			previewAbort?.abort();
			previewVersion++;
			searching.value = loadingMore.value = previewPending.value = false;
			return;
		}
		if (searchAbort?.signal.aborted) searchAbort = new AbortController();
		if (previewAbort?.signal.aborted) previewAbort = new AbortController();
		if (searchAbort && query.value) void checkSearch(searchAbort);
		if (previewAbort && preview.value) void poll(previewAbort, previewVersion);
	}
	function dispose() {
		active = false;
		clearSearch();
		closePreview();
	}
	onScopeDispose(dispose);
	return {
		searchUpdate,
		previewUpdate,
		refreshing,
		refreshError,
		searchExpired,
		applySearchUpdate,
		applyPreviewUpdate,
		setActive,
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
		search,
		loadMore,
		clearSearch,
		openPreview,
		closePreview,
		dispose,
	};
}
