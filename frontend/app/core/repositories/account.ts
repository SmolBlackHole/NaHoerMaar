// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type {
	AccountSession,
	AppearanceUpdate,
	ProfileUpdate,
	StatisticsPeriod,
	UserProfile,
} from "../models/account";

export function createAccountRepository(request: Transport) {
	return {
		loginUrl: "/api/auth/discord",
		session: (signal?: AbortSignal) =>
			request<AccountSession>("/api/auth/session", { allowSignedOut: true, signal }),
		profile: (period: StatisticsPeriod = "30d", signal?: AbortSignal) =>
			request<UserProfile>(`/api/users/me?period=${period}`, { signal }),
		updateProfile: (body: ProfileUpdate) =>
			request<UserProfile>("/api/users/me/profile", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			}),
		updateAppearance: (body: AppearanceUpdate) =>
			request<UserProfile>("/api/users/me/appearance", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			}),
		logout: () => request<void>("/api/auth/logout", { method: "POST" }),
	};
}

export type AccountRepository = ReturnType<typeof createAccountRepository>;
