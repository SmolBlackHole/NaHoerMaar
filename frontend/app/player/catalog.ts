import { ref, shallowRef } from "vue";
import type { CatalogTrack, PlaylistPreview, SearchPage, SearchSource } from "../../shared/catalog";

export function createCatalogClient(request: typeof fetch = (...args) => fetch(...args)) {
	const results = shallowRef<CatalogTrack[]>([]);
	const query = ref("");
	const searchSource = ref<SearchSource>("youtube_music");
	const searching = ref(false);
	const loadingMore = ref(false);
	const nextOffset = ref<number | null>(null);
	const searchError = ref("");
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
			throw new Error(
				typeof data.detail === "string"
					? data.detail
					: "YouTube could not be reached. Try again.",
			);
		return data as T;
	}

	async function search(text: string) {
		searchAbort?.abort();
		query.value = text;
		results.value = [];
		nextOffset.value = null;
		loadingMore.value = false;
		await searchPage(0);
	}

	async function loadMore() {
		if (searching.value || loadingMore.value || nextOffset.value === null) return;
		await searchPage(nextOffset.value);
	}

	async function searchPage(offset: number) {
		const abort = new AbortController();
		searchAbort = abort;
		searchError.value = "";
		const busy = offset === 0 ? searching : loadingMore;
		busy.value = true;
		try {
			const found = await json<SearchPage>(
				`/api/catalog/search?q=${encodeURIComponent(query.value)}&offset=${offset}&source=${searchSource.value}`,
				{
					signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35_000)]),
				},
			);
			if (!abort.signal.aborted) {
				const seen = new Set(results.value.map((item) => item.video_id));
				const fresh = found.entries.filter((item) => {
					if (!item.video_id) return true;
					if (seen.has(item.video_id)) return false;
					seen.add(item.video_id);
					return true;
				});
				results.value = [...results.value, ...fresh];
				nextOffset.value = found.next_offset;
			}
		} catch (error) {
			if (!abort.signal.aborted)
				searchError.value =
					error instanceof Error ? error.message : "Search failed. Try again.";
		} finally {
			if (searchAbort === abort) busy.value = false;
		}
	}

	function clearSearch() {
		searchAbort?.abort();
		searching.value = false;
		loadingMore.value = false;
		nextOffset.value = null;
		results.value = [];
		query.value = "";
		searchError.value = "";
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
		previewPending.value = false;
		previewUrl.value = "";
		previewError.value = "";
	}

	async function poll(id: string, version: number) {
		try {
			const value = await json<PlaylistPreview>(`/api/youtube/playlists/${id}`);
			if (version !== previewVersion) return;
			preview.value = value;
			if (value.state === "loading") timer = setTimeout(() => void poll(id, version), 750);
		} catch (error) {
			if (version === previewVersion)
				previewError.value =
					error instanceof Error
						? error.message
						: "Could not update the playlist. Open it again.";
		}
	}

	async function openPreview(url: string) {
		closePreview();
		const version = previewVersion;
		const id = crypto.randomUUID();
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
			preview.value = value;
			if (value.state === "loading") void poll(id, version);
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
			preview.value = value;
			previewError.value = "";
			return value.state === "ready";
		} catch (error) {
			if (version === previewVersion)
				previewError.value =
					error instanceof Error ? error.message : "Open the playlist again.";
			return false;
		}
	}

	function dispose() {
		clearSearch();
		closePreview();
	}
	return {
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
