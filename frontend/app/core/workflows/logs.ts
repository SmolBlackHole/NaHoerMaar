// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { LogPage } from "../models/logs";
import { createQueryState } from "./queryState";

export function createLogsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const page = createQueryState<LogPage>(authority);

	function loadLatest(limit = 200) {
		return page.load((signal) => client.logs.recent(undefined, limit, signal));
	}

	async function poll(limit = 200) {
		const previous = page.data.value;
		const next = await page.load((signal) =>
			client.logs.recent(previous?.cursor, limit, signal),
		);
		if (!next || !previous) return next;
		const entries = new Map(
			[...previous.entries, ...next.entries].map((entry) => [entry.id, entry]),
		);
		const combined = {
			entries: [...entries.values()].sort((left, right) => left.id - right.id),
			cursor: Math.max(previous.cursor, next.cursor),
		};
		page.set(combined);
		return combined;
	}

	return { page, loadLatest, poll, dispose: page.dispose };
}
