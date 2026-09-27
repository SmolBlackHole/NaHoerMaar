// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { computed } from "vue";
import type { SessionAuthority } from "../api/transport";
import { createQueryState } from "./queryState";

export interface CursorPage<T> {
	entries: T[];
	next_cursor: string | null;
}

type CursorReader<TPage> = (
	limit: number,
	cursor: string | undefined,
	signal: AbortSignal,
) => Promise<TPage>;

/** Shared lifecycle for descending cursor feeds with stable item identities. */
export function createCursorPagination<T, TPage extends CursorPage<T>>(
	authority: SessionAuthority,
	read: CursorReader<TPage>,
	key: (entry: T) => string,
) {
	const page = createQueryState<TPage>(authority);
	const hasMore = computed(() => Boolean(page.data.value?.next_cursor));

	function load(limit = 20) {
		return page.load((signal) => read(limit, undefined, signal));
	}

	async function more(limit = 20) {
		const previous = page.data.value;
		if (!previous?.next_cursor) return previous;
		const next = await page.load((signal) => read(limit, previous.next_cursor!, signal));
		if (!next) return null;
		page.set(withEntries(next, merge(previous.entries, next.entries, key), next.next_cursor));
		return page.data.value;
	}

	async function refresh(limit = 20) {
		const previous = page.data.value;
		const latest = await page.load((signal) => read(limit, undefined, signal));
		if (!latest || !previous) return latest;
		page.set(
			withEntries(latest, merge(latest.entries, previous.entries, key), previous.next_cursor),
		);
		return page.data.value;
	}

	return { page, hasMore, load, more, refresh, dispose: page.dispose };
}

function merge<T>(first: T[], second: T[], key: (entry: T) => string): T[] {
	const seen = new Set<string>();
	return [...first, ...second].filter((entry) => {
		const identity = key(entry);
		if (seen.has(identity)) return false;
		seen.add(identity);
		return true;
	});
}

function withEntries<T, TPage extends CursorPage<T>>(
	page: TPage,
	entries: T[],
	nextCursor: string | null,
): TPage {
	return { ...page, entries, next_cursor: nextCursor };
}
