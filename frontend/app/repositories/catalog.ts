import type { components } from "../../shared/api.generated";
import type { DiscoveryPage, Track } from "../../shared/engine";
import type { HttpTransport } from "./transport";
type Schema = components["schemas"];
export function createCatalogRepository(json: HttpTransport) {
	return {
		resolveTrack: (sourceUrl: string, signal?: AbortSignal) =>
			json<Track>("/api/catalog/track", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ source_url: sourceUrl } satisfies Schema["LinkInput"]),
				signal,
			}),
		openPlaylist: (sourceUrl: string, refresh = true, signal?: AbortSignal) =>
			json<DiscoveryPage>("/api/catalog/playlist", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					source_url: sourceUrl,
					refresh,
				} satisfies Schema["PlaylistInput"]),
				signal,
			}),
		search: (query: string, provider: string, signal?: AbortSignal) =>
			json<DiscoveryPage>(
				`/api/catalog/search?q=${encodeURIComponent(query)}&provider=${encodeURIComponent(provider)}&refresh=true`,
				{ signal },
			),
		snapshot: (
			kind: "search" | "playlist",
			version: string,
			offset = 0,
			limit = 20,
			signal?: AbortSignal,
		) =>
			json<DiscoveryPage>(`/api/catalog/${kind}/${version}?offset=${offset}&limit=${limit}`, {
				signal,
			}),
	};
}
export type CatalogRepository = ReturnType<typeof createCatalogRepository>;
