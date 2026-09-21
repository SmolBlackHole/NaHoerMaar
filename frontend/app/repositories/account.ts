import type { components } from "../../shared/api.generated";
import type { ListenerSession } from "../../shared/session";
import type { Appearance } from "../../shared/appearance";
import type { HttpTransport } from "./transport";
export function createAccountRepository(json: HttpTransport) {
	return {
		session: () =>
			json<ListenerSession>("/api/auth/session", { anonymous: true, timeoutMs: 15_000 }),
		saveProfile: (body: components["schemas"]["ProfileInput"]) =>
			json<ListenerSession>("/api/profile", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
				timeoutMs: 15_000,
			}),
		saveAppearance: (body: Appearance, signal?: AbortSignal) =>
			json<Appearance>("/api/profile/appearance", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
				signal,
				timeoutMs: 15_000,
			}),
		logout: () => json<void>("/api/auth/logout", { method: "POST", timeoutMs: 15_000 }),
	};
}
export type AccountRepository = ReturnType<typeof createAccountRepository>;
