// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type {
	AccountSession,
	Account,
	CurrentUserUpdate,
	ListenerProfile,
	StatisticsPeriod,
} from "../models/account";

export function createAccountRepository(request: Transport) {
	return {
		loginUrl: "/api/auth/discord",
		session: (signal?: AbortSignal) =>
			request((api) => api.GET("/api/auth/session", { signal })),
		account: (signal?: AbortSignal) => request((api) => api.GET("/api/users/me", { signal })),
		profile: (period: StatisticsPeriod = "30d", signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/profiles/me", { params: { query: { period } }, signal }),
			),
		userProfile: (userId: string, period: StatisticsPeriod = "30d", signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/profiles/{user_id}", {
					params: { path: { user_id: userId }, query: { period } },
					signal,
				}),
			),
		update: (body: CurrentUserUpdate) => request((api) => api.PATCH("/api/users/me", { body })),
		logout: () => request((api) => api.POST("/api/auth/logout")),
	};
}

export type AccountRepository = ReturnType<typeof createAccountRepository>;
