// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { ref } from "vue";
import { SessionLost, type SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { AccessEvent, AccessState, DiscordMembers } from "../models/access";
import { createQueryState } from "./queryState";

export interface AccessPage {
	access: AccessState;
	members: DiscordMembers["members"];
}

export function createAccessWorkflow(client: BackendClient, authority: SessionAuthority) {
	const page = createQueryState<AccessPage>(authority);
	const pendingDiscordIds = ref<string[]>([]);
	const mutationError = ref<string | null>(null);
	const unsubscribe = authority.subscribe(() => {
		mutationError.value = null;
		pendingDiscordIds.value = [];
	});

	async function change(discordId: string, mutate: () => Promise<AccessEvent | null>) {
		if (pendingDiscordIds.value.includes(discordId)) return null;
		const generation = authority.current().generation;
		pendingDiscordIds.value = [...pendingDiscordIds.value, discordId];
		mutationError.value = null;
		try {
			const event = await mutate();
			if (generation !== authority.current().generation) return null;
			await load();
			return event;
		} catch (failure) {
			if (generation === authority.current().generation && !(failure instanceof SessionLost))
				mutationError.value = failure instanceof Error ? failure.message : String(failure);
			return null;
		} finally {
			if (generation === authority.current().generation)
				pendingDiscordIds.value = pendingDiscordIds.value.filter((id) => id !== discordId);
		}
	}

	function load() {
		return page.load(async (signal) => {
			const [access, members] = await Promise.all([
				client.access.state(100, signal),
				client.access.members(signal),
			]);
			return { access, members: members.members };
		});
	}

	return {
		page,
		pendingDiscordIds,
		mutationError,
		load,
		grant: (discordId: string) => change(discordId, () => client.access.grant(discordId)),
		revoke: (discordId: string) => change(discordId, () => client.access.revoke(discordId)),
		dispose() {
			page.dispose();
			unsubscribe();
		},
	};
}
