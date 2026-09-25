// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { ref } from "vue";
import { SessionLost, type SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type {
	AppearanceUpdate,
	ProfileUpdate,
	StatisticsPeriod,
	UserProfile,
} from "../models/account";
import { createQueryState } from "./queryState";

export function createProfileWorkflow(client: BackendClient, authority: SessionAuthority) {
	const profile = createQueryState<UserProfile>(authority);
	const saving = ref(false);
	const mutationError = ref<string | null>(null);
	const unsubscribe = authority.subscribe(() => {
		mutationError.value = null;
		saving.value = false;
	});

	function loadMine(period: StatisticsPeriod = "30d") {
		return profile.load((signal) => client.account.profile(period, signal));
	}

	function loadUser(userId: string, period: StatisticsPeriod = "30d") {
		return profile.load((signal) => client.account.userProfile(userId, period, signal));
	}

	async function save(write: () => Promise<UserProfile>) {
		if (saving.value) return null;
		const generation = authority.current().generation;
		const previous = profile.data.value;
		saving.value = true;
		mutationError.value = null;
		try {
			const updated = await write();
			if (generation !== authority.current().generation) return null;
			profile.set(
				previous
					? {
							...updated,
							statistics: previous.statistics,
							recent_tracks: previous.recent_tracks,
						}
					: updated,
			);
			return profile.data.value;
		} catch (failure) {
			if (generation === authority.current().generation && !(failure instanceof SessionLost))
				mutationError.value = failure instanceof Error ? failure.message : String(failure);
			return null;
		} finally {
			if (generation === authority.current().generation) saving.value = false;
		}
	}

	return {
		profile,
		saving,
		mutationError,
		loadMine,
		loadUser,
		updateProfile: (body: ProfileUpdate) => save(() => client.account.updateProfile(body)),
		updateAppearance: (body: AppearanceUpdate) =>
			save(() => client.account.updateAppearance(body)),
		dispose() {
			profile.dispose();
			unsubscribe();
		},
	};
}
