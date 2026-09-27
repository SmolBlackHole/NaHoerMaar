// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import { createQueryState } from "./queryState";

export interface NumberedPage {
	page: number;
	page_size: number;
	total: number;
	page_count: number;
	snapshot: string | null;
}

export interface PageRequest<TFilters extends object = Record<string, never>> {
	page?: number;
	pageSize?: number;
	query?: string;
	filters?: TFilters;
	newSnapshot?: boolean;
}

export interface ResolvedPageRequest<TFilters extends object> {
	page: number;
	pageSize: number;
	query?: string;
	filters: TFilters;
	snapshot?: string;
}

/** Stable numbered pagination whose first read pins a server-side snapshot. */
export function createPagePagination<
	TPage extends NumberedPage,
	TFilters extends object = Record<string, never>,
>(
	authority: SessionAuthority,
	read: (request: ResolvedPageRequest<TFilters>, signal: AbortSignal) => Promise<TPage>,
) {
	const state = createQueryState<TPage>(authority);
	let snapshot: string | undefined;
	let activeCriteria = "";

	async function load(options: PageRequest<TFilters> = {}) {
		const query = options.query?.trim() ?? "";
		const filters = options.filters ?? ({} as TFilters);
		const criteria = JSON.stringify([query, filters]);
		if (options.newSnapshot || criteria !== activeCriteria) snapshot = undefined;
		activeCriteria = criteria;
		const result = await state.load((signal) =>
			read(
				{
					page: options.page ?? 1,
					pageSize: options.pageSize ?? 20,
					query: query || undefined,
					filters,
					snapshot,
				},
				signal,
			),
		);
		if (result) snapshot = result.snapshot ?? undefined;
		return result;
	}

	return {
		page: state,
		load,
		dispose: state.dispose,
	};
}
