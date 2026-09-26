// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type {
	AccountSession,
	Account,
	AppearanceUpdate,
	ListenerProfile,
	ProfileUpdate,
	StatisticsPeriod,
} from "../models/account";

export function createAccountRepository(request: Transport) {
	return {
		loginUrl: "/api/auth/discord",
		session: (signal?: AbortSignal) =>
			request<AccountSession>("/api/auth/session", { allowSignedOut: true, signal }),
		account: (signal?: AbortSignal) => request<Account>("/api/users/me", { signal }),
		profile: (period: StatisticsPeriod = "30d", signal?: AbortSignal) =>
			request<ListenerProfile>(`/api/profiles/me?period=${period}`, { signal }),
		userProfile: (userId: string, period: StatisticsPeriod = "30d", signal?: AbortSignal) =>
			request<ListenerProfile>(
				`/api/profiles/${encodeURIComponent(userId)}?period=${period}`,
				{
					signal,
				},
			),
		updateProfile: (body: ProfileUpdate) =>
			request<Account>("/api/users/me/profile", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			}),
		updateAppearance: (body: AppearanceUpdate) =>
			request<Account>("/api/users/me/appearance", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			}),
		logout: () => request<void>("/api/auth/logout", { method: "POST" }),
	};
}

export type AccountRepository = ReturnType<typeof createAccountRepository>;
