<script setup lang="ts">
import {
	canControl,
	formatTime,
	playbackPosition,
	trackTitle,
	trackArtwork,
	youtubeVideoId,
} from "#shared/player";
import { usePlayerStore } from "~/stores/player";

const player = usePlayerStore();
const cinema = defineModel<boolean>("cinema", { default: false });
const { icons } = useTheme();
const now = ref(Date.now());
useIntervalFn(() => {
	if (player.connection === "live") now.value = Date.now();
}, 500);
const current = computed(() => player.snapshot?.current ?? null);
const videoId = computed(() => (current.value ? youtubeVideoId(current.value.source_url) : null));
const artwork = computed(() => trackArtwork(current.value));
const preview = ref<"artwork" | "video">("artwork");
const videoFailed = ref(false);
watch(
	() => player.snapshot?.playback_id,
	() => {
		videoFailed.value = false;
	},
);
useEventListener("keydown", (event) => {
	if (event.key === "Escape") cinema.value = false;
});
const position = computed(() =>
	player.snapshot ? playbackPosition(player.snapshot, now.value) : 0,
);
const action = computed(() => (player.snapshot?.state === "playing" ? "pause" : "play"));
const label = computed(
	() =>
		({
			idle: "Play",
			loading: "Loading",
			playing: "Pause",
			paused: "Resume",
			error: "Retry track",
		})[player.snapshot?.state ?? "idle"],
);
const stateLabel = computed(
	() =>
		({
			idle: "Ready when you are",
			loading: "Loading audio…",
			playing: "Now playing",
			paused: "Paused",
			error: "Couldn't play this track",
		})[player.snapshot?.state ?? "idle"],
);
const volume = ref(100);
watch(
	() => player.snapshot?.volume,
	(value) => {
		if (value !== undefined) volume.value = Math.round(value * 100);
	},
	{ immediate: true },
);
async function setVolume() {
	await player.mutate("/api/player/volume", "PUT", { volume: volume.value / 100 });
	volume.value = Math.round((player.snapshot?.volume ?? 1) * 100);
}
</script>

<template>
	<section
		aria-labelledby="now-playing-heading"
		class="player-pane relative isolate space-y-6 p-5 sm:p-8"
		:class="cinema && 'cinema-pane'"
	>
		<img
			v-if="cinema && artwork"
			:src="artwork"
			alt=""
			aria-hidden="true"
			class="cinema-atmosphere"
		/>
		<div class="flex items-center justify-between gap-3">
			<h2 id="now-playing-heading" class="text-highlighted text-lg font-semibold">
				{{ stateLabel }}
			</h2>
			<UIcon
				v-if="player.snapshot?.state === 'loading'"
				:name="icons.loading"
				class="size-5 motion-safe:animate-spin"
			/>
			<UButton
				:label="cinema ? 'Exit cinema' : 'Cinema'"
				:icon="icons.system"
				variant="ghost"
				color="neutral"
				:aria-pressed="cinema"
				@click="cinema = !cinema"
			/>
		</div>
		<div class="space-y-3">
			<div v-if="videoId" class="flex gap-1" role="group" aria-label="Preview mode">
				<UButton
					label="Artwork"
					:aria-pressed="preview === 'artwork'"
					:variant="preview === 'artwork' ? 'soft' : 'ghost'"
					color="neutral"
					@click="preview = 'artwork'"
				/>
				<UButton
					label="Video"
					:aria-pressed="preview === 'video'"
					:variant="preview === 'video' ? 'soft' : 'ghost'"
					color="neutral"
					@click="
						preview = 'video';
						videoFailed = false;
					"
				/>
			</div>
			<PlayerVideo
				v-if="preview === 'video' && videoId && !videoFailed"
				:key="player.snapshot?.playback_id ?? videoId"
				:video-id="videoId"
				:title="current ? trackTitle(current) : 'YouTube preview'"
				:position="position"
				:state="player.snapshot?.state ?? 'idle'"
				:live="player.connection === 'live'"
				@failed="videoFailed = true"
			/>
			<PlayerTrackArtwork v-else :entry="current" large />
			<p v-if="preview === 'video' && videoFailed" role="status" class="text-muted text-sm">
				This video can't be shown here. You can still open it on YouTube.
			</p>
		</div>
		<div class="min-h-16 space-y-2">
			<h3 class="text-highlighted text-2xl font-semibold leading-tight break-words">
				<a
					v-if="current"
					:href="current.source_url"
					target="_blank"
					rel="noopener noreferrer"
					class="hover:underline underline-offset-4"
					>{{ trackTitle(current) }}</a
				>
				<template v-else>Pick the next track</template>
			</h3>
			<p class="text-muted text-sm">
				<PlayerArtistLink v-if="current" :entry="current" /><template v-else
					>Add a YouTube link to the queue, join a channel and press play.</template
				>
			</p>
			<UButton
				v-if="current"
				:to="current.source_url"
				target="_blank"
				rel="noopener noreferrer"
				:trailing-icon="icons.external"
				label="Open on YouTube"
				variant="link"
				color="neutral"
				class="px-0"
			/>
		</div>
		<div class="space-y-2">
			<progress
				v-if="current?.duration_seconds"
				class="playback-progress w-full"
				:max="current.duration_seconds"
				:value="position"
				aria-label="Playback progress"
			/>
			<div v-else class="bg-elevated h-1 w-full rounded-full" />
			<div class="text-muted flex justify-between text-xs tabular-nums">
				<span>{{ formatTime(position) }}</span>
				<span>{{ formatTime(current?.duration_seconds ?? null) }}</span>
			</div>
		</div>
		<div class="flex items-center justify-center gap-4">
			<UTooltip text="Stop and return track to queue">
				<UButton
					:icon="icons.stop"
					aria-label="Stop playback"
					color="neutral"
					variant="ghost"
					class="size-11 justify-center"
					:disabled="!player.enabled || !canControl(player.snapshot, 'stop')"
					@click="player.control('stop')"
				/>
			</UTooltip>
			<UButton
				:icon="action === 'pause' ? icons.pause : icons.play"
				:aria-label="label"
				:label="label"
				size="xl"
				class="min-h-12 min-w-32 justify-center"
				:loading="player.snapshot?.state === 'loading'"
				:disabled="!player.enabled || !canControl(player.snapshot, action)"
				@click="player.control(action)"
			/>
			<UTooltip text="Skip track">
				<UButton
					:icon="icons.skip"
					aria-label="Skip track"
					color="neutral"
					variant="ghost"
					class="size-11 justify-center"
					:disabled="!player.enabled || !canControl(player.snapshot, 'skip')"
					@click="player.control('skip')"
				/>
			</UTooltip>
		</div>
		<div class="space-y-3 border-t border-default pt-6">
			<div class="flex justify-between text-sm">
				<label for="bot-volume" class="flex items-center gap-2"
					><UIcon :name="icons.volume" class="size-4" /> Bot volume</label
				>
				<output for="bot-volume" class="text-muted tabular-nums">{{ volume }}%</output>
			</div>
			<input
				id="bot-volume"
				v-model.number="volume"
				type="range"
				min="0"
				max="100"
				step="1"
				class="volume-slider w-full"
				:disabled="!player.enabled"
				:aria-valuetext="`${volume} percent`"
				@change="setVolume"
			/>
			<p class="text-muted text-xs">Changes the volume for everyone in the channel.</p>
		</div>
	</section>
</template>

<style scoped>
.cinema-pane {
	position: relative;
	width: 100%;
	max-width: 64rem;
	margin-inline: auto;
}
.cinema-pane :deep(.text-muted) {
	color: var(--ui-text-toned);
}
.cinema-atmosphere {
	position: absolute;
	inset: 0;
	z-index: -1;
	width: 100%;
	height: 75%;
	object-fit: cover;
	filter: blur(64px);
	opacity: 0.2;
	pointer-events: none;
}
</style>
