// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { RecentPlayback } from "../models/listening";

export function createListeningRepository(request: Transport) {
	return {
		recent: (limit = 20, signal?: AbortSignal) =>
			request<RecentPlayback[]>(`/api/listening/recent?limit=${limit}`, { signal }),
	};
}

export type ListeningRepository = ReturnType<typeof createListeningRepository>;
