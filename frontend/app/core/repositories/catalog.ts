// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { Discovery, DiscoveryKind, Track } from "../models/catalog";

export function createCatalogRepository(request: Transport) {
	return {
		search: (
			query: string,
			options: { limit?: number; provider?: string; refresh?: boolean } = {},
			signal?: AbortSignal,
		) => {
			const params = new URLSearchParams({ q: query });
			if (options.limit !== undefined) params.set("limit", String(options.limit));
			if (options.provider) params.set("provider", options.provider);
			if (options.refresh) params.set("refresh", "true");
			return request<Discovery>(`/api/catalog/search?${params}`, { signal });
		},
		playlist: (
			url: string,
			options: { limit?: number; provider?: string; refresh?: boolean } = {},
			signal?: AbortSignal,
		) => {
			const params = new URLSearchParams({ url });
			if (options.limit !== undefined) params.set("limit", String(options.limit));
			if (options.provider) params.set("provider", options.provider);
			if (options.refresh) params.set("refresh", "true");
			return request<Discovery>(`/api/catalog/playlist?${params}`, { signal });
		},
		link: (url: string, signal?: AbortSignal) => {
			const params = new URLSearchParams({ url });
			return request<Track>(`/api/catalog/link?${params}`, { signal });
		},
		snapshot: (
			kind: DiscoveryKind,
			version: string,
			offset = 0,
			limit = 20,
			signal?: AbortSignal,
		) => {
			const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
			return request<Discovery>(
				`/api/catalog/${kind}/${encodeURIComponent(version)}?${params}`,
				{ signal },
			);
		},
	};
}

export type CatalogRepository = ReturnType<typeof createCatalogRepository>;
