import avatars from "../../backend/src/nahormaar_backend/avatars.json";
import type { components } from "./api.generated";

export type ListenerProfile = components["schemas"]["Contributor"];

export const PROFILE_KEY = "nahormaar-profile";

export function randomAvatar(previous?: string): string {
	const choices = avatars.filter((id) => id !== previous);
	return choices[Math.floor(Math.random() * choices.length)]!;
}

export function parseProfile(raw: string | null): ListenerProfile | null {
	if (!raw) return null;
	try {
		const value = JSON.parse(raw) as Partial<ListenerProfile> | null;
		if (
			!value ||
			typeof value.id !== "string" ||
			!/^[\da-f-]{36}$/i.test(value.id) ||
			typeof value.name !== "string" ||
			!value.name.trim() ||
			value.name.trim().length > 32 ||
			typeof value.avatar !== "string" ||
			!avatars.includes(value.avatar)
		)
			return null;
		return { id: value.id, name: value.name.trim(), avatar: value.avatar };
	} catch {
		return null;
	}
}
