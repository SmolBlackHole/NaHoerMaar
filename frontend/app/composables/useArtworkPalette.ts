import { computed, onMounted, watch } from "vue";
import { useSettingsStore } from "~/stores/settings";
import { loadArtworkPalette, type ArtworkPalette } from "~/utils/artworkPalette";

export function useArtworkPalette() {
	const player = useNuxtApp().$backendCore.stores.usePlayerStore();
	const settings = useSettingsStore();
	const consent = useConsentStore();
	const palette = useState<ArtworkPalette | null>("artwork-palette", () => null);
	const artwork = computed(() => player.currentTrack?.track.artwork_url ?? null);
	onMounted(() => {
		watch(
			() => (consent.youtube && settings.settings.artwork_colors ? artwork.value : null),
			async (url, _previous, onCleanup) => {
				const controller = new AbortController();
				onCleanup(() => controller.abort());
				if (!url) {
					palette.value = null;
					return;
				}
				const next = await loadArtworkPalette(url, controller.signal);
				if (!controller.signal.aborted && next) palette.value = next;
			},
			{ immediate: true },
		);
	});
	return palette;
}
