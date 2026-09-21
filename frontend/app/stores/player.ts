import { defineStore } from "pinia";
import { createPlayerClient } from "~/player/client";
import { useProfileStore } from "~/stores/profile";

export const usePlayerStore = defineStore("player", () => {
	const profile = useProfileStore();
	const client = createPlayerClient(profile.request, undefined, {
		lost: profile.lost,
		check: profile.restore,
		userId: () => profile.profile?.id,
	});
	return client;
});
