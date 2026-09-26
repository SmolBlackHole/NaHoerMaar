export function usePlaybackPosition() {
	const player = useNuxtApp().$backendCore.stores.usePlayerStore();
	const now = ref(Date.now());
	useIntervalFn(() => {
		if (player.connection === "live") now.value = Date.now();
	}, 1000);
	watch(
		() => player.state,
		() => {
			now.value = Date.now();
		},
	);
	function at(time: number) {
		const runtime = player.state?.runtime;
		if (!runtime) return 0;
		const elapsed =
			["playing", "transitioning"].includes(runtime.phase) && runtime.position_updated_at
				? Math.max(0, (time - Date.parse(runtime.position_updated_at)) / 1000)
				: 0;
		return Math.min(
			runtime.duration_seconds ?? Number.POSITIVE_INFINITY,
			runtime.position_seconds + elapsed,
		);
	}
	const position = computed(() => at(now.value));
	function currentPosition() {
		return at(Date.now());
	}
	return { now, position, currentPosition };
}
