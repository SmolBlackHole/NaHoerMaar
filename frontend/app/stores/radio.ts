import { defineStore } from "pinia";
import { createRadioClient } from "~/player/radio";
import { useProfileStore } from "~/stores/profile";

export const useRadioStore = defineStore("radio", () => {
	const profile = useProfileStore();
	return createRadioClient(profile.request);
});
