import type { MediaReference } from "./engine";
import type { ListenerProfile } from "./profile";

export interface RadioSource {
	kind: MediaReference["kind"];
	source_url: string;
	title: string;
}
export interface RadioPreview {
	seed: MediaReference;
}
export interface RadioStatus {
	state: "off" | "active" | "loading" | "waiting";
	generation: string | null;
	seed: MediaReference | null;
	title: string | null;
	initiator: ListenerProfile | null;
	error: string | null;
}
