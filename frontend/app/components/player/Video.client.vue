<script setup lang="ts">
import { loadYouTube, type YouTubePlayer } from "~/player/youtube";
import type { PlaybackState } from "#shared/player";
const props = defineProps<{
	videoId: string;
	title: string;
	position: number;
	state: PlaybackState;
	interactive: boolean;
}>();
const emit = defineEmits<{
	failed: [];
	ready: [value: boolean];
	blocked: [value: boolean];
	ended: [];
}>();
const host = ref<HTMLElement>();
const ready = ref(false);
let embed: YouTubePlayer | undefined;
let active = true;
let readyTimeout: ReturnType<typeof setTimeout> | undefined;

function updateInteraction() {
	if (embed) embed.getIframe().tabIndex = props.interactive ? 0 : -1;
}
function followPlayer() {
	if (!ready.value || !embed) return;
	if (props.state === "playing") embed.playVideo();
	else embed.pauseVideo();
}
function align() {
	if (!ready.value || !embed) return;
	embed.seekTo(props.position, true);
	followPlayer();
}
function startPreview() {
	embed?.mute();
	align();
	emit("blocked", false);
}
defineExpose({ align, startPreview });
function fail() {
	clearTimeout(readyTimeout);
	if (active) emit("failed");
}
onMounted(async () => {
	emit("ready", false);
	emit("blocked", false);
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
				controls: 1,
				disablekb: 0,
				rel: 0,
				iv_load_policy: 3,
			},
			events: {
				onReady(event) {
					if (!active) return;
					clearTimeout(readyTimeout);
					embed = event.target;
					embed.getIframe().title = props.title;
					updateInteraction();
					embed.mute();
					ready.value = true;
					emit("ready", true);
					followPlayer();
				},
				onError: fail,
				onStateChange(event) {
					if (active && event.data === 0) emit("ended");
				},
				onAutoplayBlocked() {
					if (active) emit("blocked", true);
				},
			},
		});
	} catch {
		fail();
	}
});
watch(() => props.state, followPlayer);
watch(() => props.interactive, updateInteraction);
onBeforeUnmount(() => {
	active = false;
	clearTimeout(readyTimeout);
	embed?.destroy();
});
</script>

<template>
	<div
		ref="host"
		class="media-video"
		:class="{ 'is-interactive': interactive }"
		:aria-busy="!ready"
		:aria-hidden="!interactive"
	/>
</template>

<style scoped>
.media-video {
	position: absolute;
	left: 50%;
	top: 50%;
	width: max(100cqw, 177.778cqh);
	height: max(100cqh, 56.25cqw);
	transform: translate(-50%, -50%);
	pointer-events: none;
}
.media-video.is-interactive {
	inset: 0;
	width: 100%;
	height: 100%;
	transform: none;
	pointer-events: auto;
}
.media-video :deep(iframe) {
	display: block;
	width: 100%;
	height: 100%;
}
</style>
