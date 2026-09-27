// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { RecentPlaybackPage } from "../models/listening";
import { createCursorPagination } from "./cursorPagination";

export function createRecentWorkflow(client: BackendClient, authority: SessionAuthority) {
	const pagination = createCursorPagination<
		RecentPlaybackPage["entries"][number],
		RecentPlaybackPage
	>(
		authority,
		(limit, cursor, signal) => client.listening.recent(limit, cursor, signal),
		({ playback_id }) => playback_id,
	);
	return {
		recent: pagination.page,
		hasMore: pagination.hasMore,
		load: pagination.load,
		more: pagination.more,
		dispose: pagination.dispose,
	};
}
