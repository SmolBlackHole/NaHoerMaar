// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { RecentPlayback } from "../models/listening";
import { createQueryState } from "./queryState";

export function createRecentWorkflow(client: BackendClient, authority: SessionAuthority) {
	const recent = createQueryState<RecentPlayback[]>(authority);
	return {
		recent,
		load: (limit = 20) => recent.load((signal) => client.listening.recent(limit, signal)),
		dispose: recent.dispose,
	};
}
