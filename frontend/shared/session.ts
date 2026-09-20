import type { ListenerProfile } from "./profile";
import type { Appearance } from "./appearance";

export type SessionStatus =
	| "checking"
	| "authenticated"
	| "signed_out"
	| "forbidden"
	| "unavailable";

export interface ListenerSession {
	appearance: Appearance;
	profile: ListenerProfile;
	profile_complete: boolean;
	csrf_token: string;
	expires_at: number;
}
