import { ref, shallowRef } from "vue";
import type { RadioPreview, RadioSource } from "../../shared/radio";
import type { Track, DiscoveryPage, MediaReference } from "../../shared/engine";

export function createRadioClient(request: typeof fetch) {
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
			const response = await request(`/api/catalog/${value.kind}`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({ source_url: value.source_url }),
				signal: AbortSignal.any([controller.signal, AbortSignal.timeout(40_000)]),
			});
			if (!response.ok) {
				const data = await response.json();
				throw new Error(
					typeof data.detail === "string"
						? data.detail
						: "Could not load radio. Try again.",
				);
			}
			const data: Track | DiscoveryPage = await response.json();
			const seed: MediaReference =
				value.kind === "playlist"
					? (data as DiscoveryPage).playlist!
					: {
							identity: (data as Track).identity,
							kind: "track",
							source_url: (data as Track).source_url,
						};
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
	return { source, preview, loading, error, version, open, dispose };
}
