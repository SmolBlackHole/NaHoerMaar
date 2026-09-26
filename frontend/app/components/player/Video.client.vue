<script setup lang="ts">
import { loadYouTube, type YouTubePlayer } from "~/player/youtube";
const props = defineProps<{
	videoId: string;
	title: string;
	getPosition: () => number;
	active: boolean;
	state: string;
	interactive: boolean;
	volume: number;
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
let disposed = false;
let initializing = false;
let readyTimeout: ReturnType<typeof setTimeout> | undefined;

function updateInteraction() {
	if (embed) embed.getIframe().tabIndex = props.interactive ? 0 : -1;
}
function followPlayer() {
	if (!ready.value || !embed) return;
	if (props.active && ["playing", "transitioning"].includes(props.state)) embed.playVideo();
	else embed.pauseVideo();
}
function applyVolume() {
	if (!ready.value || !embed) return;
	embed.setVolume(props.volume);
	if (props.volume === 0) embed.mute();
	else embed.unMute();
}
function align() {
	if (!ready.value || !embed) return;
	if (props.active) embed.seekTo(props.getPosition(), true);
	followPlayer();
}
function startPreview() {
	applyVolume();
	align();
	emit("blocked", false);
}
defineExpose({ align, startPreview });
function fail() {
	clearTimeout(readyTimeout);
	if (!disposed) emit("failed");
}
async function initialize() {
	if (disposed || !props.active || !host.value || embed || initializing) return;
	initializing = true;
	emit("ready", false);
	emit("blocked", false);
	try {
		const api = await loadYouTube();
		if (disposed || !props.active || !host.value) return;
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
				start: Math.floor(props.getPosition()),
				controls: 1,
				disablekb: 0,
				rel: 0,
				iv_load_policy: 3,
			},
			events: {
				onReady(event) {
					if (disposed) return;
					clearTimeout(readyTimeout);
					embed = event.target;
					embed.getIframe().title = props.title;
					updateInteraction();
					ready.value = true;
					applyVolume();
					emit("ready", true);
					align();
				},
				onError: fail,
				onStateChange(event) {
					if (disposed) return;
					if (!props.active && event.data === 1) embed?.pauseVideo();
					if (props.active && event.data === 0) emit("ended");
				},
				onAutoplayBlocked() {
					if (!disposed && props.active) emit("blocked", true);
				},
			},
		});
	} catch {
		fail();
	} finally {
		initializing = false;
	}
}
onMounted(initialize);
watch(
	() => props.active,
	(visible) => {
		if (!visible) followPlayer();
		else if (ready.value) {
			emit("blocked", false);
			align();
		} else void initialize();
	},
);
watch(() => props.state, followPlayer);
watch(() => props.volume, applyVolume);
watch(() => props.interactive, updateInteraction);
onBeforeUnmount(() => {
	disposed = true;
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
