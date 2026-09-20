import { defineStore } from "pinia";
import { createPlayerClient } from "~/player/client";
import { useProfileStore } from "~/stores/profile";

export const usePlayerStore = defineStore("player", () => {
	const client = createPlayerClient();
	const profile = useProfileStore();
	function add(sourceUrl: string) {
		if (!profile.profile) return Promise.resolve(false);
		return client.mutate("/api/queue", "POST", {
			source_url: sourceUrl,
			added_by: { ...profile.profile },
		});
	}
	function addMany(sourceUrls: string[]) {
		if (!profile.profile) return Promise.resolve(false);
		return client.mutate("/api/queue/batch", "POST", {
			source_urls: sourceUrls,
			added_by: { ...profile.profile },
		});
	}
	return { ...client, add, addMany };
});
