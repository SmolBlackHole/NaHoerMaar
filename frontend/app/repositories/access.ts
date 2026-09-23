import type { components } from "../../shared/api.generated";
import type { HttpTransport } from "./transport";

type AccessState = components["schemas"]["AccessView"];

export function createAccessRepository(json: HttpTransport) {
	return {
		state: () => json<AccessState>("/api/admin/access", { timeoutMs: 15_000 }),
		members: () =>
			json<components["schemas"]["DiscordMembersView"]>("/api/admin/access/members", {
				timeoutMs: 15_000,
			}),
		grant: (discordId: string) =>
			json<AccessState>(`/api/admin/access/${encodeURIComponent(discordId)}`, {
				method: "PUT",
				timeoutMs: 15_000,
			}),
		revoke: (discordId: string) =>
			json<AccessState>(`/api/admin/access/${encodeURIComponent(discordId)}`, {
				method: "DELETE",
				timeoutMs: 15_000,
			}),
	};
}

export type AccessRepository = ReturnType<typeof createAccessRepository>;
