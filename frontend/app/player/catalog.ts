import { ref, shallowRef } from "vue";
import type { CatalogTrack, PlaylistPreview, SearchPage, SearchSource } from "../../shared/catalog";

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
	let previewId: string | undefined;
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function json<T>(path: string, options?: RequestInit): Promise<T> {
		const response = await request(path, { signal: AbortSignal.timeout(35_000), ...options });
		const data = await response.json();
		if (!response.ok)
			throw new CatalogError(
				response.status,
				typeof data.detail === "string"
					? data.detail
					: "YouTube could not be reached. Try again.",
			);
		return data as T;
	}

	function searchUrl(offset: number, snapshot?: string | null) {
		return `/api/catalog/search?q=${encodeURIComponent(query.value)}&offset=${offset}&source=${searchSource.value}${snapshot ? `&snapshot_id=${snapshot}` : ""}`;
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
			const found = await json<SearchPage>(
				searchUrl(offset, offset ? searchSnapshot.value : null),
				{ signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]) },
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
				searchExpired.value = error instanceof CatalogError && error.status === 410;
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
			const current = await json<SearchPage>(searchUrl(0, searchSnapshot.value), {
				signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
			});
			if (abort.signal.aborted) return;
			refreshing.value = current.refreshing;
			refreshError.value = current.refresh_error || "";
			if (current.latest_snapshot_id !== searchSnapshot.value) {
				const next = await json<SearchPage>(searchUrl(0, current.latest_snapshot_id), {
					signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
				});
				if (abort.signal.aborted) return;
				searchUpdate.value = next;
			}
			if (current.refreshing && active)
				searchTimer = setTimeout(() => void checkSearch(abort), 1000);
		} catch (error) {
			if (abort.signal.aborted) return;
			searchExpired.value = error instanceof CatalogError && error.status === 410;
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

	async function cancelRemote(id: string) {
		return json<PlaylistPreview>(`/api/youtube/playlists/${id}`, { method: "DELETE" });
	}

	function closePreview() {
		previewVersion++;
		clearTimeout(timer);
		if (previewId && (preview.value?.state === "loading" || previewPending.value))
			void cancelRemote(previewId).catch(() => {});
		previewId = undefined;
		preview.value = null;
		previewUpdate.value = null;
		previewPending.value = false;
		previewUrl.value = "";
		previewError.value = "";
	}

	function receivePreview(value: PlaylistPreview) {
		refreshing.value = value.refreshing;
		refreshError.value = value.refresh_error || "";
		if (preview.value?.state === "ready" && value.state === "loading") return;
		if (
			preview.value?.state === "ready" &&
			value.state === "ready" &&
			preview.value.snapshot_id !== value.snapshot_id
		)
			previewUpdate.value = value;
		else preview.value = value;
	}
	function applyPreviewUpdate() {
		if (previewUpdate.value) preview.value = previewUpdate.value;
		previewUpdate.value = null;
	}
	async function poll(id: string, version: number) {
		try {
			const value = await json<PlaylistPreview>(`/api/youtube/playlists/${id}`);
			if (version !== previewVersion) return;
			receivePreview(value);
			if (active && (value.state === "loading" || value.refreshing))
				timer = setTimeout(() => void poll(id, version), 750);
		} catch (error) {
			if (version === previewVersion)
				previewError.value =
					error instanceof Error
						? error.message
						: "Could not update the playlist. Open it again.";
		}
	}

	async function openPreview(url: string, preserve = false) {
		const previous = preserve ? preview.value : null;
		const previousId = preserve ? previewId : undefined;
		closePreview();
		if (previous) preview.value = previous;
		const version = previewVersion;
		const id = previousId || crypto.randomUUID();
		previewId = id;
		previewUrl.value = url;
		previewPending.value = true;
		try {
			const value = await json<PlaylistPreview>("/api/youtube/playlists", {
				method: "POST",
				headers: { "Content-Type": "application/json", "Idempotency-Key": id },
				body: JSON.stringify({ source_url: url }),
			});
			if (version !== previewVersion) {
				void cancelRemote(id).catch(() => {});
				return;
			}
			if (previous) preview.value = { ...previous, id: value.id };
			receivePreview(value);
			if (active && (value.state === "loading" || value.refreshing)) void poll(id, version);
		} catch (error) {
			if (version === previewVersion)
				previewError.value =
					error instanceof Error ? error.message : "Could not open the playlist.";
			void cancelRemote(id).catch(() => {});
		} finally {
			if (version === previewVersion) previewPending.value = false;
		}
	}

	async function cancelPreview() {
		const id = previewId;
		if (!id || previewPending.value) return;
		const version = ++previewVersion;
		clearTimeout(timer);
		previewPending.value = true;
		try {
			const value = await cancelRemote(id);
			if (version === previewVersion) preview.value = value;
		} catch (error) {
			if (version === previewVersion)
				previewError.value =
					error instanceof Error ? error.message : "Could not cancel. Try again.";
		} finally {
			if (version === previewVersion) previewPending.value = false;
		}
	}

	async function validatePreview(): Promise<boolean> {
		const id = previewId;
		const version = previewVersion;
		if (!id) return false;
		try {
			const value = await json<PlaylistPreview>(`/api/youtube/playlists/${id}`);
			if (version !== previewVersion) return false;
			receivePreview(value);
			previewError.value = "";
			return value.state === "ready";
		} catch (error) {
			if (version === previewVersion)
				previewError.value =
					error instanceof Error ? error.message : "Open the playlist again.";
			return false;
		}
	}

	function setActive(value: boolean) {
		active = value;
		clearTimeout(searchTimer);
		clearTimeout(timer);
		if (!active) return;
		if (searchAbort && refreshing.value) void checkSearch(searchAbort);
		if (previewId && (preview.value?.state === "loading" || refreshing.value))
			void poll(previewId, previewVersion);
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
		cancelPreview,
		validatePreview,
		dispose,
	};
}
