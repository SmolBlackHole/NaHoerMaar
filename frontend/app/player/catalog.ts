import { ref, shallowRef } from "vue";
import {
	catalogPage,
	type CatalogTrack,
	type PlaylistPreview,
	type SearchPage,
	type SearchSource,
} from "../../shared/catalog";
import type { DiscoveryPage } from "../../shared/engine";

class CatalogError extends Error {
	constructor(
		public status: number,
		message: string,
	) {
		super(message);
	}
}
export function createCatalogClient(request: typeof fetch = (...args) => fetch(...args)) {
	const results = shallowRef<CatalogTrack[]>([]);
	const query = ref("");
	const searchSource = ref<SearchSource>("youtube_music");
	const searching = ref(false);
	const loadingMore = ref(false);
	const nextOffset = ref<number | null>(null);
	const searchError = ref("");
	const searchExpired = ref(false);
	const searchSnapshot = ref<string | null>(null);
	const searchUpdate = shallowRef<SearchPage | null>(null);
	const previewUpdate = shallowRef<PlaylistPreview | null>(null);
	const refreshing = ref(false);
	const refreshError = ref("");
	let searchedSource: SearchSource = "youtube_music";
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	let active = true;
	const preview = shallowRef<PlaylistPreview | null>(null);
	const previewUrl = ref("");
	const previewError = ref("");
	const previewPending = ref(false);
	let searchAbort: AbortController | undefined;
	let previewVersion = 0;
	let previewAbort: AbortController | undefined;
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function json<T>(path: string, options?: RequestInit): Promise<T> {
		const response = await request(path, { signal: AbortSignal.timeout(35_000), ...options });
		const data = await response.json();
		if (!response.ok)
			throw new CatalogError(
				response.status,
				[404, 410].includes(response.status)
					? "These results expired. Search or open the playlist again."
					: typeof data.detail === "string"
						? data.detail
						: "YouTube could not be reached. Try again.",
			);
		return data as T;
	}

	function searchUrl(offset: number, snapshot?: string | null) {
		return snapshot
			? `/api/catalog/search/${snapshot}?offset=${offset}`
			: `/api/catalog/search?q=${encodeURIComponent(query.value)}&provider=${searchSource.value}&refresh=true`;
	}
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

	function append(page: SearchPage) {
		const seen = new Set(results.value.map((item) => item.video_id));
		results.value = [
			...results.value,
			...page.entries.filter((item) => {
				if (!item.video_id) return true;
				if (seen.has(item.video_id)) return false;
				seen.add(item.video_id);
				return true;
			}),
		];
		nextOffset.value = page.next_offset;
		searchSnapshot.value = page.snapshot_id;
	}

	function observe(page: SearchPage, abort: AbortController) {
		refreshing.value = page.refreshing;
		refreshError.value = page.refresh_error || "";
		if (active && (page.refreshing || page.latest_snapshot_id !== page.snapshot_id)) {
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
				await json<DiscoveryPage>(searchUrl(offset, offset ? searchSnapshot.value : null), {
					signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
				}),
			);
			if (abort.signal.aborted) return;
			if (preserve && results.value.length) {
				if (found.snapshot_id !== searchSnapshot.value) searchUpdate.value = found;
			} else {
				if (!offset) results.value = [];
				append(found);
			}
			observe(found, abort);
		} catch (error) {
			if (!abort.signal.aborted) {
				searchExpired.value =
					error instanceof CatalogError && [404, 410].includes(error.status);
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
				await json<DiscoveryPage>(searchUrl(0, searchSnapshot.value), {
					signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
				}),
			);
			if (abort.signal.aborted) return;
			refreshing.value = current.refreshing;
			refreshError.value = current.refresh_error || "";
			if (current.latest_snapshot_id !== searchSnapshot.value) {
				const next = catalogPage(
					await json<DiscoveryPage>(searchUrl(0, current.latest_snapshot_id), {
						signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
					}),
				);
				if (abort.signal.aborted) return;
				searchUpdate.value = next;
			}
			if (current.refreshing && active)
				searchTimer = setTimeout(() => void checkSearch(abort), 1000);
		} catch (error) {
			if (abort.signal.aborted) return;
			searchExpired.value =
				error instanceof CatalogError && [404, 410].includes(error.status);
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

	async function readPlaylist(
		page: DiscoveryPage,
		url: string,
		abort: AbortController,
	): Promise<PlaylistPreview> {
		const options = { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]) };
		// One bounded version contains at most 100 occurrences, including duplicates.
		const all =
			page.total > page.entries.length
				? await json<DiscoveryPage>(
						`/api/catalog/playlist/${page.version}?limit=100`,
						options,
					)
				: page;
		const found = catalogPage(all);
		return {
			id: page.version,
			snapshot_id: page.version,
			source_url: url,
			state: "ready",
			title: page.title ?? preview.value?.title ?? null,
			entries: found.entries,
			limit: 100,
			truncated: all.total >= 100,
			error: all.error,
			refreshing: found.refreshing,
			refresh_error: found.refresh_error,
		};
	}
	function receivePreview(value: PlaylistPreview) {
		refreshing.value = value.refreshing;
		refreshError.value = value.refresh_error || "";
		if (preview.value && value.snapshot_id !== preview.value.snapshot_id)
			previewUpdate.value = value;
		else preview.value = value;
	}
	function applyPreviewUpdate() {
		if (previewUpdate.value) preview.value = previewUpdate.value;
		previewUpdate.value = null;
	}
	async function poll(abort: AbortController, version: number) {
		if (!active || abort.signal.aborted || !preview.value) return;
		try {
			const options = {
				signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
			};
			const current = await json<DiscoveryPage>(
				`/api/catalog/playlist/${preview.value.id}`,
				options,
			);
			if (version !== previewVersion) return;
			refreshing.value = current.refresh.refreshing;
			refreshError.value = current.refresh.error || "";
			if (current.refresh.latest_version !== preview.value.id) {
				const next = await json<DiscoveryPage>(
					`/api/catalog/playlist/${current.refresh.latest_version}?limit=100`,
					options,
				);
				const value = await readPlaylist(next, previewUrl.value, abort);
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
			const page = await json<DiscoveryPage>("/api/catalog/playlist", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ source_url: url, refresh: true }),
				signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
			});
			if (version !== previewVersion) return;
			const value = await readPlaylist(page, url, abort);
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
		active = value;
		clearTimeout(searchTimer);
		clearTimeout(timer);
		if (!active) return;
		if (searchAbort && query.value) void checkSearch(searchAbort);
		if (previewAbort && preview.value) void poll(previewAbort, previewVersion);
	}
	function dispose() {
		active = false;
		clearSearch();
		closePreview();
	}
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
		cancelPreview: closePreview,
		dispose,
	};
}
