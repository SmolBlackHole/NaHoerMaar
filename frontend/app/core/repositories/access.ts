// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
export function createAccessRepository(request: Transport) {
	return {
		state: (historyLimit = 100, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/access", {
					params: { query: { history_limit: historyLimit } },
					signal,
				}),
			),
		members: (filters: { query?: string; guildId?: string } = {}, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/access/members", {
					params: {
						query: { q: filters.query, guild_id: filters.guildId },
					},
					signal,
				}),
			),
		grant: (discordId: string) =>
			request((api) =>
				api.PUT("/api/access/{discord_id}", {
					params: { path: { discord_id: discordId } },
				}),
			),
		revoke: (discordId: string) =>
			request((api) =>
				api.DELETE("/api/access/{discord_id}", {
					params: { path: { discord_id: discordId } },
				}),
			),
	};
}

export type AccessRepository = ReturnType<typeof createAccessRepository>;
