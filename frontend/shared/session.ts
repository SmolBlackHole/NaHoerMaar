import type { ListenerProfile } from "./profile";

export type SessionStatus =
	| "checking"
	| "authenticated"
	| "signed_out"
	| "forbidden"
	| "unavailable";

export interface ListenerSession {
	profile: ListenerProfile;
	profile_complete: boolean;
	csrf_token: string;
	expires_at: number;
}
