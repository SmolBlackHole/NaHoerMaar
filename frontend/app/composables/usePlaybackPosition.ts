import { playbackPosition } from "#shared/player";
import { usePlayerStore } from "~/stores/player";

export function usePlaybackPosition() {
	const player = usePlayerStore();
	const now = ref(Date.now());
	useIntervalFn(() => {
		if (player.connection === "live") now.value = Date.now();
	}, 1000);
	watch(
		() => player.snapshot,
		() => {
			now.value = Date.now();
		},
	);
	const position = computed(() =>
		player.snapshot ? playbackPosition(player.snapshot, now.value) : 0,
	);
	function currentPosition() {
		return player.snapshot ? playbackPosition(player.snapshot, Date.now()) : 0;
	}
	return { now, position, currentPosition };
}
