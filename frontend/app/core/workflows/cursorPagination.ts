// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { computed } from "vue";
import type { SessionAuthority } from "../api/transport";
import { createQueryState } from "./queryState";

export interface CursorPage<T> {
	items: T[];
	page_size: number;
	next_cursor: string | null;
}

type CursorReader<TPage> = (
	pageSize: number,
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

	function load(pageSize = 20) {
		return page.load((signal) => read(pageSize, undefined, signal));
	}

	async function more(pageSize = 20) {
		const previous = page.data.value;
		if (!previous?.next_cursor) return previous;
		const next = await page.load((signal) => read(pageSize, previous.next_cursor!, signal));
		if (!next) return null;
		page.set(withItems(next, merge(previous.items, next.items, key), next.next_cursor));
		return page.data.value;
	}

	async function refresh(pageSize = 20) {
		const previous = page.data.value;
		const latest = await page.load((signal) => read(pageSize, undefined, signal));
		if (!latest || !previous) return latest;
		page.set(withItems(latest, merge(latest.items, previous.items, key), previous.next_cursor));
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

function withItems<T, TPage extends CursorPage<T>>(
	page: TPage,
	items: T[],
	nextCursor: string | null,
): TPage {
	return { ...page, items, next_cursor: nextCursor };
}
