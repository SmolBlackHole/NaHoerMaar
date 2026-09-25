// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { AccessEvent, AccessState, DiscordMembers } from "../models/access";

export function createAccessRepository(request: Transport) {
	return {
		state: (historyLimit = 100, signal?: AbortSignal) =>
			request<AccessState>(`/api/access?history_limit=${historyLimit}`, { signal }),
		members: (signal?: AbortSignal) =>
			request<DiscordMembers>("/api/access/members", { signal }),
		grant: (discordId: string) =>
			request<AccessEvent | null>(`/api/access/${encodeURIComponent(discordId)}`, {
				method: "PUT",
			}),
		revoke: (discordId: string) =>
			request<AccessEvent | null>(`/api/access/${encodeURIComponent(discordId)}`, {
				method: "DELETE",
			}),
	};
}

export type AccessRepository = ReturnType<typeof createAccessRepository>;
