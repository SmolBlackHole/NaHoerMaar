<script setup lang="ts">
import { loadYouTube, type YouTubePlayer } from "~/player/youtube";
import type { PlaybackState } from "#shared/player";
const props = defineProps<{
	videoId: string;
	title: string;
	position: number;
	state: PlaybackState;
	live: boolean;
}>();
const emit = defineEmits<{ failed: [] }>();
const host = ref<HTMLElement>();
const ready = ref(false);
const blocked = ref(false);
let embed: YouTubePlayer | undefined;
let active = true;
let readyTimeout: ReturnType<typeof setTimeout> | undefined;

function followPlayer() {
	if (!ready.value || !embed) return;
	if (props.state === "playing" && props.live) embed.playVideo();
	else embed.pauseVideo();
}
function align() {
	embed?.seekTo(props.position, true);
	followPlayer();
}
function fail() {
	clearTimeout(readyTimeout);
	if (active) emit("failed");
}
onMounted(async () => {
	try {
		const api = await loadYouTube();
		if (!active || !host.value) return;
		const target = document.createElement("div");
		host.value.append(target);
		readyTimeout = setTimeout(fail, 15000);
		embed = new api.Player(target, {
			videoId: props.videoId,
			width: "100%",
			height: "100%",
			playerVars: {
				origin: location.origin,
				playsinline: 1,
				autoplay: 0,
				start: Math.floor(props.position),
			},
			events: {
				onReady(event) {
					if (!active) return;
					clearTimeout(readyTimeout);
					embed = event.target;
					embed.getIframe().title = props.title;
					embed.mute();
					ready.value = true;
					followPlayer();
				},
				onError: fail,
				onAutoplayBlocked() {
					blocked.value = true;
				},
			},
		});
	} catch {
		fail();
	}
});
watch(() => [props.state, props.live], followPlayer);
onBeforeUnmount(() => {
	active = false;
	clearTimeout(readyTimeout);
	embed?.destroy();
});
</script>

<template>
	<div class="space-y-3">
		<div
			ref="host"
			class="video-preview aspect-video min-h-[200px] overflow-hidden rounded-lg bg-black"
			:aria-busy="!ready"
		/>
		<div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
			<p role="status">
				{{
					!ready
						? "Loading video preview…"
						: blocked
							? "Press play in the video to start the preview."
							: "Video starts muted. Audio plays in Discord."
				}}
			</p>
			<UButton
				label="Match player position"
				variant="link"
				color="neutral"
				size="xs"
				class="p-0"
				:disabled="!ready || !live"
				@click="align"
			/>
		</div>
	</div>
</template>

<style scoped>
.video-preview :deep(iframe) {
	width: 100%;
	height: 100%;
	min-height: 200px;
}
</style>
