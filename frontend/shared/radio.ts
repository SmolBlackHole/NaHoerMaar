import type { CatalogTrack } from "./catalog";
import type { ListenerProfile } from "./profile";

export interface RadioSeed {
	kind: "track" | "playlist";
	identifier: string;
	title: string;
}
export interface RadioSource {
	kind: RadioSeed["kind"];
	source_url: string;
	title: string;
}
export interface RadioPreview {
	id: string;
	seed: RadioSeed;
	entries: CatalogTrack[];
}
export interface RadioStatus {
	state: "off" | "active" | "loading" | "waiting";
	session_id: string | null;
	seed: RadioSeed | null;
	initiator: ListenerProfile | null;
	error: string | null;
	event_id: string | null;
	action: "started" | "stopped" | "retried" | null;
	actor: ListenerProfile | null;
}
