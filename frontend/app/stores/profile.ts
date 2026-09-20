import { defineStore } from "pinia";
import { PROFILE_KEY, parseProfile, randomAvatar } from "#shared/profile";
import { createSessionClient } from "~/auth/client";

export const useProfileStore = defineStore("profile", () => {
	const client = createSessionClient();
	const suggestion = ref<{ name: string; avatar: string } | null>(null);
	let channel: BroadcastChannel | undefined;
	let listening = false;
	function refresh() {
		void client.restore();
	}
	async function restore() {
		if (!listening && import.meta.client) {
			listening = true;
			try {
				const old = parseProfile(localStorage.getItem(PROFILE_KEY));
				if (old) suggestion.value = { name: old.name, avatar: old.avatar };
			} catch {
				/* A legacy profile is only an optional suggestion. */
			}
			if (typeof BroadcastChannel !== "undefined") {
				channel = new BroadcastChannel("nahormaar-account");
				channel.onmessage = refresh;
			}
			window.addEventListener("focus", refresh);
		}
		await client.restore();
	}
	async function save(name: string, avatar: string) {
		if (!(await client.save(name, avatar))) return false;
		suggestion.value = null;
		try {
			localStorage.removeItem(PROFILE_KEY);
		} catch {
			/* Optional legacy storage. */
		}
		channel?.postMessage("profile");
		return true;
	}
	async function signOut() {
		if (await client.signOut()) channel?.postMessage("logout");
	}
	onScopeDispose(() => {
		client.dispose();
		channel?.close();
		if (import.meta.client) window.removeEventListener("focus", refresh);
	});
	return { ...client, suggestion, restore, save, signOut, randomAvatar };
});
