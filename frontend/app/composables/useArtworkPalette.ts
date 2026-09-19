import { computed, onMounted, shallowRef, watch } from "vue";
import { trackArtwork } from "#shared/player";
import { usePlayerStore } from "~/stores/player";
import { useSettingsStore } from "~/stores/settings";
import { loadArtworkPalette, type ArtworkPalette } from "~/utils/artworkPalette";

export function useArtworkPalette() {
	const player = usePlayerStore();
	const settings = useSettingsStore();
	const palette = shallowRef<ArtworkPalette | null>(null);
	const artwork = computed(() => trackArtwork(player.snapshot?.current ?? null));
	onMounted(() => {
		watch(
			() => (settings.settings.artworkColors ? artwork.value : null),
			async (url, _previous, onCleanup) => {
				const controller = new AbortController();
				onCleanup(() => controller.abort());
				if (!url) {
					palette.value = null;
					return;
				}
				const next = await loadArtworkPalette(url, controller.signal);
				if (!controller.signal.aborted) palette.value = next;
			},
			{ immediate: true },
		);
	});
	return palette;
}
