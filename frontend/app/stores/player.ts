import { defineStore } from "pinia";
import { createPlayerClient } from "~/player/client";
import { useProfileStore } from "~/stores/profile";

export const usePlayerStore = defineStore("player", () => {
	const profile = useProfileStore();
	const client = createPlayerClient(profile.request, undefined, {
		lost: profile.lost,
		check: profile.restore,
	});
	function add(sourceUrl: string) {
		if (!profile.profile) return Promise.resolve(false);
		return client.mutate("/api/queue", "POST", {
			source_url: sourceUrl,
		});
	}
	function addMany(sourceUrls: string[], skipDuplicates = false) {
		if (!profile.profile) return Promise.resolve(false);
		return client.mutate("/api/queue/batch", "POST", {
			source_urls: sourceUrls,
			skip_duplicates: skipDuplicates,
		});
	}
	return { ...client, add, addMany };
});
