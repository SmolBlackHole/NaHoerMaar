import { ref, shallowRef, onScopeDispose } from "vue";
import type { RadioPreview, RadioSource } from "../../shared/radio";
import { defineStore } from "pinia";
import { useRepositories } from "../repositories";
import type { MediaReference } from "../../shared/engine";

export const useRadioPreviewStore = defineStore("radioPreview", () => {
	const { catalog: api } = useRepositories();
	const source = shallowRef<RadioSource | null>(null);
	const preview = shallowRef<RadioPreview | null>(null);
	const loading = ref(false);
	const error = ref("");
	const version = ref(0);
	let abort: AbortController | undefined;
	async function open(value: RadioSource) {
		abort?.abort();
		const current = ++version.value;
		const controller = new AbortController();
		abort = controller;
		source.value = value;
		preview.value = null;
		error.value = "";
		loading.value = true;
		try {
			let seed: MediaReference;
			if (value.reference) seed = value.reference;
			else if (value.kind === "playlist") {
				const page = await api.openPlaylist(value.source_url, false, controller.signal);
				if (!page.playlist) throw new Error("The playlist could not be resolved.");
				seed = page.playlist.reference;
			} else {
				const track = await api.resolveTrack(value.source_url, controller.signal);
				seed = { kind: "track", identity: track.identity, source_url: track.source_url };
			}
			if (current === version.value) preview.value = { seed };
		} catch (reason) {
			if (current === version.value)
				error.value =
					reason instanceof Error && reason.name !== "TimeoutError"
						? reason.message
						: "Radio took too long. Try again.";
		} finally {
			if (current === version.value) loading.value = false;
		}
	}
	function dispose() {
		abort?.abort();
		version.value++;
		source.value = null;
		preview.value = null;
		error.value = "";
		loading.value = false;
	}
	onScopeDispose(dispose);
	return { source, preview, loading, error, version, open, dispose };
});
