import type { components } from "./api.generated";
import type { Appearance } from "./appearance";

export type SessionStatus =
	"checking" | "authenticated" | "signed_out" | "forbidden" | "unavailable";

export type ListenerSession = Omit<components["schemas"]["AccountView"], "appearance"> & {
	appearance: Appearance;
};
