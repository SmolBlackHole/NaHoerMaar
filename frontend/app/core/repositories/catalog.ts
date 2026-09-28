// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { Discovery, Track } from "../models/catalog";

export function createCatalogRepository(request: Transport) {
	return {
		search: (
			query: string,
			options: { limit?: number; provider?: string; refresh?: boolean } = {},
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.GET("/api/catalog/search", {
					params: {
						query: {
							q: query,
							limit: options.limit,
							provider: options.provider,
							refresh: options.refresh || undefined,
						},
					},
					signal,
				}),
			),
		playlist: (
			url: string,
			options: { limit?: number; provider?: string; refresh?: boolean } = {},
			signal?: AbortSignal,
		) =>
			request((api) =>
				api.GET("/api/catalog/playlist", {
					params: {
						query: {
							url,
							limit: options.limit,
							provider: options.provider,
							refresh: options.refresh || undefined,
						},
					},
					signal,
				}),
			),
		link: (url: string, signal?: AbortSignal) =>
			request((api) => api.GET("/api/catalog/link", { params: { query: { url } }, signal })),
		discovery: (version: string, offset = 0, pageSize = 20, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/catalog/discoveries/{version}", {
					params: { path: { version }, query: { offset, page_size: pageSize } },
					signal,
				}),
			),
		continueDiscovery: (version: string, offset: number, pageSize = 20, signal?: AbortSignal) =>
			request((api) =>
				api.POST("/api/catalog/discoveries/{version}/continuations", {
					params: { path: { version }, query: { offset, page_size: pageSize } },
					signal,
				}),
			),
	};
}

export type CatalogRepository = ReturnType<typeof createCatalogRepository>;
