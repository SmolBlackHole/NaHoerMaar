// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { LogPage } from "../models/logs";

export function createLogsRepository(request: Transport) {
	return {
		recent: (after?: number, limit = 200, signal?: AbortSignal) => {
			const params = new URLSearchParams({ limit: String(limit) });
			if (after !== undefined) params.set("after", String(after));
			return request<LogPage>(`/api/logs?${params}`, { signal });
		},
	};
}

export type LogsRepository = ReturnType<typeof createLogsRepository>;
