// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { shallowRef } from "vue";
import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { Discovery, Track } from "../models/catalog";
import { createQueryState } from "./queryState";

export function createDiscoveryWorkflow(client: BackendClient, authority: SessionAuthority) {
	const results = createQueryState<Discovery>(authority);
	const linkedTrack = createQueryState<Track>(authority);
	const lastRead = shallowRef<(() => Promise<Discovery | null>) | null>(null);

	function search(
		query: string,
		options: { limit?: number; provider?: string; refresh?: boolean } = {},
	) {
		const read = (refresh: boolean) =>
			results.load((signal) => client.catalog.search(query, { ...options, refresh }, signal));
		lastRead.value = () => read(true);
		return read(options.refresh ?? false);
	}

	function playlist(
		url: string,
		options: { limit?: number; provider?: string; refresh?: boolean } = {},
	) {
		const read = (refresh: boolean) =>
			results.load((signal) => client.catalog.playlist(url, { ...options, refresh }, signal));
		lastRead.value = () => read(true);
		return read(options.refresh ?? false);
	}

	function snapshot(version: string, offset = 0, pageSize = 20) {
		const read = (nextOffset: number) =>
			results.load((signal) =>
				client.catalog.discovery(version, nextOffset, pageSize, signal),
			);
		lastRead.value = () => read(offset);
		return read(offset);
	}

	async function more(pageSize = 20) {
		const previous = results.data.value;
		if (!previous || (previous.next_offset === null && !previous.source_has_more)) return null;
		const extendsProviderSnapshot = previous.next_offset === null;
		const page = await results.load((signal) =>
			extendsProviderSnapshot
				? client.catalog.continueDiscovery(
						previous.version,
						previous.total,
						pageSize,
						signal,
					)
				: client.catalog.discovery(
						previous.version,
						previous.next_offset!,
						pageSize,
						signal,
					),
		);
		if (!page) return page;
		if (!extendsProviderSnapshot && page.version !== previous.version) return page;
		const items = new Map(
			[...previous.items, ...page.items].map((entry) => [entry.position, entry]),
		);
		results.set({
			...page,
			items: [...items.values()].sort((a, b) => a.position - b.position),
		});
		return results.data.value;
	}

	function resolveLink(url: string) {
		return linkedTrack.load((signal) => client.catalog.link(url, signal));
	}

	return {
		results,
		linkedTrack,
		search,
		playlist,
		snapshot,
		more,
		resolveLink,
		refresh: () => lastRead.value?.() ?? Promise.resolve(null),
		dispose() {
			results.dispose();
			linkedTrack.dispose();
		},
	};
}
