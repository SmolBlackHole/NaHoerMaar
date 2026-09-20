import { defineStore } from "pinia";

export const useConsentStore = defineStore("consent", () => {
	const choice = useCookie<string | null>("nahormaar-consent", {
		default: () => null,
		maxAge: 60 * 60 * 24 * 180,
		sameSite: "lax",
		path: "/",
		secure: useRequestURL().protocol === "https:",
	});
	const ready = ref(false);
	const open = ref(false);
	const decided = computed(() => ["v1:necessary", "v1:youtube"].includes(choice.value ?? ""));
	const youtube = computed(() => ready.value && choice.value === "v1:youtube");
	const visible = computed(() => ready.value && (!decided.value || open.value));
	function choose(allowYouTube: boolean) {
		choice.value = allowYouTube ? "v1:youtube" : "v1:necessary";
		open.value = false;
	}
	return { ready, open, decided, youtube, visible, choose };
});
