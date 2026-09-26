// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { RecentPlaybackPage } from "../models/listening";
import { createQueryState } from "./queryState";

export function createRecentWorkflow(client: BackendClient, authority: SessionAuthority) {
	const recent = createQueryState<RecentPlaybackPage>(authority);
	async function load(limit = 20) {
		return recent.load((signal) => client.listening.recent(limit, undefined, signal));
	}
	async function more(limit = 20) {
		const previous = recent.data.value;
		if (!previous?.next_cursor) return previous;
		const page = await recent.load((signal) =>
			client.listening.recent(limit, previous.next_cursor ?? undefined, signal),
		);
		if (!page) return null;
		recent.set({
			entries: [...previous.entries, ...page.entries],
			next_cursor: page.next_cursor,
		});
		return recent.data.value;
	}
	return {
		recent,
		load,
		more,
		dispose: recent.dispose,
	};
}
