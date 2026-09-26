// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { Account, ListenerProfile, StatisticsPeriod } from "../models/account";
import { createQueryState } from "./queryState";

export function createProfileWorkflow(client: BackendClient, authority: SessionAuthority) {
	const profile = createQueryState<ListenerProfile>(authority);

	function loadMine(period: StatisticsPeriod = "30d") {
		return profile.load((signal) => client.account.profile(period, signal));
	}

	function loadUser(userId: string, period: StatisticsPeriod = "30d") {
		return profile.load((signal) => client.account.userProfile(userId, period, signal));
	}

	function mergeAccount(account: Account) {
		const current = profile.data.value;
		if (!current || current.id !== account.id) return;
		profile.set({
			...current,
			...account,
			statistics: current.statistics,
			recent_tracks: current.recent_tracks,
		});
	}

	return {
		profile,
		loadMine,
		loadUser,
		mergeAccount,
		dispose: profile.dispose,
	};
}
