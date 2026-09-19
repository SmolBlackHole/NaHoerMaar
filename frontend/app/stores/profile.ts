import { defineStore } from "pinia";
import { PROFILE_KEY, parseProfile, randomAvatar, type ListenerProfile } from "#shared/profile";

export const useProfileStore = defineStore("profile", () => {
	const profile = ref<ListenerProfile | null>(null);
	const ready = ref(false);
	const storageUnavailable = ref(false);

	function restore() {
		try {
			profile.value = parseProfile(localStorage.getItem(PROFILE_KEY));
		} catch {
			storageUnavailable.value = true;
		}
		ready.value = true;
	}

	function save(name: string, avatar: string) {
		const next = parseProfile(
			JSON.stringify({ id: profile.value?.id ?? crypto.randomUUID(), name, avatar }),
		);
		if (!next) return false;
		profile.value = next;
		try {
			localStorage.setItem(PROFILE_KEY, JSON.stringify(next));
			storageUnavailable.value = false;
		} catch {
			storageUnavailable.value = true;
		}
		return true;
	}

	function signOut() {
		profile.value = null;
		try {
			localStorage.removeItem(PROFILE_KEY);
		} catch {
			storageUnavailable.value = true;
		}
	}

	return { profile, ready, storageUnavailable, restore, save, signOut, randomAvatar };
});
