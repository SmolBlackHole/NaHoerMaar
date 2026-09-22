import { computed, ref, shallowRef, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import { PROFILE_KEY, parseProfile, randomAvatar } from "../../shared/profile";
import { useRepositories } from "../repositories";
import { SessionLost } from "../repositories/transport";
import type { ListenerSession, SessionStatus } from "../../shared/session";

export const useProfileStore = defineStore("profile", () => {
	const { account } = useRepositories();
	const suggestion = ref<{ name: string; avatar: string } | null>(null);
	let channel: BroadcastChannel | undefined;
	let listening = false;
	const session = shallowRef<ListenerSession | null>(null);
	const status = ref<SessionStatus>("checking");
	const error = ref("");
	const busy = ref(false);
	const profile = computed(() => session.value?.profile ?? null);
	const appearance = computed(() => session.value?.appearance ?? null);
	const profileComplete = computed(() => session.value?.profile_complete ?? false);
	const ready = computed(() => status.value !== "checking");
	const generation = ref(0);
	const credentials = computed(() => ({
		generation: generation.value,
		csrfToken: session.value?.csrf_token ?? null,
	}));
	let checking: Promise<void> | undefined;
	let expiry: ReturnType<typeof setTimeout> | undefined;

	function lost(code: string) {
		generation.value++;
		clearTimeout(expiry);
		session.value = null;
		status.value =
			code === "access_denied"
				? "forbidden"
				: ["access_unavailable", "auth_unavailable"].includes(code)
					? "unavailable"
					: "signed_out";
	}

	function accept(value: ListenerSession) {
		if (session.value && session.value.profile.id !== value.profile.id) generation.value++;
		session.value = value;
		status.value = "authenticated";
		clearTimeout(expiry);
		expiry = setTimeout(
			() => lost("signed_out"),
			Math.max(0, value.expires_at * 1000 - Date.now()),
		);
	}

	function refresh() {
		void restore();
	}
	async function restore() {
		if (!listening && typeof window !== "undefined") {
			listening = true;
			try {
				const old = parseProfile(localStorage.getItem(PROFILE_KEY));
				if (old) suggestion.value = { name: old.name, avatar: old.avatar };
			} catch {}
			if (typeof BroadcastChannel !== "undefined") {
				channel = new BroadcastChannel("nahormaar-account");
				channel.onmessage = refresh;
			}
			window.addEventListener("focus", refresh);
		}
		if (checking) return checking;
		const version = generation.value;
		checking = (async () => {
			try {
				const value = await account.session();
				if (version === generation.value) accept(value);
			} catch (failure) {
				if (
					version === generation.value &&
					!(failure instanceof SessionLost) &&
					!session.value
				)
					lost("auth_unavailable");
			} finally {
				checking = undefined;
			}
		})();
		return checking;
	}

	async function save(name: string, avatar: string) {
		if (busy.value) return false;
		const version = generation.value;
		busy.value = true;
		error.value = "";
		try {
			const value = await account.saveProfile({ name, avatar });
			if (version !== generation.value) return false;
			accept(value);
			suggestion.value = null;
			try {
				localStorage.removeItem(PROFILE_KEY);
			} catch {}
			channel?.postMessage("profile");
			return true;
		} catch (failure) {
			if (version === generation.value && !(failure instanceof SessionLost))
				error.value = "Couldn't save your profile. Try again.";
			return false;
		} finally {
			busy.value = false;
		}
	}

	async function signOut() {
		if (busy.value) return false;
		busy.value = true;
		error.value = "";
		try {
			await account.logout();
			lost("signed_out");
			channel?.postMessage("logout");
			return true;
		} catch (failure) {
			if (failure instanceof SessionLost) return true;
			error.value = "Couldn't sign out. Check your connection and try again.";
			return false;
		} finally {
			busy.value = false;
		}
	}

	function dispose() {
		generation.value++;
		clearTimeout(expiry);
	}
	onScopeDispose(() => {
		dispose();
		channel?.close();
		if (typeof window !== "undefined") window.removeEventListener("focus", refresh);
	});
	return {
		session,
		generation,
		credentials,
		suggestion,
		randomAvatar,
		appearance,
		profile,
		profileComplete,
		ready,
		status,
		error,
		busy,
		restore,
		lost,
		save,
		signOut,
		dispose,
	};
});
