import { computed, ref, shallowRef } from "vue";
import type { ListenerSession, SessionStatus } from "../../shared/session";

export class SessionLost extends Error {}

export function createSessionClient(fetcher: typeof fetch = (...args) => fetch(...args)) {
	const session = shallowRef<ListenerSession | null>(null);
	const status = ref<SessionStatus>("checking");
	const error = ref("");
	const busy = ref(false);
	const profile = computed(() => session.value?.profile ?? null);
	const appearance = computed(() => session.value?.appearance ?? null);
	const profileComplete = computed(() => session.value?.profile_complete ?? false);
	const ready = computed(() => status.value !== "checking");
	let generation = 0;
	let checking: Promise<void> | undefined;
	let expiry: ReturnType<typeof setTimeout> | undefined;

	function lost(code: string) {
		generation++;
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
		if (session.value && session.value.profile.id !== value.profile.id) generation++;
		session.value = value;
		status.value = "authenticated";
		clearTimeout(expiry);
		expiry = setTimeout(
			() => lost("signed_out"),
			Math.max(0, value.expires_at * 1000 - Date.now()),
		);
	}

	async function rejected(response: Response, version: number): Promise<boolean> {
		if (response.ok) return false;
		const code = (
			await response
				.clone()
				.json()
				.catch(() => ({}))
		).code as string | undefined;
		if (version !== generation) return true;
		if (
			response.status === 401 ||
			["access_denied", "access_unavailable", "auth_unavailable"].includes(code ?? "")
		) {
			lost(code ?? "signed_out");
			return true;
		}
		return false;
	}

	async function restore() {
		if (checking) return checking;
		const version = generation;
		checking = (async () => {
			try {
				const response = await fetcher("/api/auth/session", {
					cache: "no-store",
					signal: AbortSignal.timeout(15_000),
				});
				if (version !== generation || (await rejected(response, version))) return;
				if (!response.ok) throw new Error("session unavailable");
				const value = (await response.json()) as ListenerSession;
				if (version === generation) accept(value);
			} catch {
				if (version === generation) lost("auth_unavailable");
			} finally {
				checking = undefined;
			}
		})();
		return checking;
	}

	const request: typeof fetch = async (input, options) => {
		if (status.value !== "authenticated" || !session.value) throw new SessionLost();
		const version = generation;
		const headers = new Headers(options?.headers);
		if (!["GET", "HEAD"].includes(options?.method ?? "GET"))
			headers.set("X-CSRF-Token", session.value.csrf_token);
		const response = await fetcher(input, { ...options, headers, cache: "no-store" });
		if (version !== generation || (await rejected(response, version))) throw new SessionLost();
		return response;
	};

	async function save(name: string, avatar: string) {
		if (busy.value) return false;
		const version = generation;
		busy.value = true;
		error.value = "";
		try {
			const response = await request("/api/profile", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ name, avatar }),
				signal: AbortSignal.timeout(15_000),
			});
			if (!response.ok) throw new Error("save failed");
			const value = (await response.json()) as ListenerSession;
			if (version !== generation) return false;
			accept(value);
			return true;
		} catch (failure) {
			if (!(failure instanceof SessionLost))
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
			const response = await request("/api/auth/logout", {
				method: "POST",
				signal: AbortSignal.timeout(15_000),
			});
			if (!response.ok) throw new Error("logout failed");
			lost("signed_out");
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
		generation++;
		clearTimeout(expiry);
	}
	return {
		appearance,
		profile,
		profileComplete,
		ready,
		status,
		error,
		busy,
		restore,
		request,
		lost,
		save,
		signOut,
		dispose,
	};
}
