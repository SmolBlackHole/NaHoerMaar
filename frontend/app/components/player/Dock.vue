<script setup lang="ts">
import { canControl, formatTime, trackTitle } from "#shared/player";
import { usePlayerStore } from "~/stores/player";

const player = usePlayerStore();
const { icons } = useTheme();
const { position } = usePlaybackPosition();
const current = computed(() => player.snapshot?.current ?? null);
const seekTarget = ref<string | null>(null);
const seekDraft = ref<{ position: number; playbackId: string } | null>(null);
const seekEnabled = computed(
	() =>
		player.enabled &&
		player.snapshot?.voice_state === "connected" &&
		!!player.snapshot.playback_id &&
		!!current.value?.duration_seconds &&
		["playing", "paused"].includes(player.snapshot.state),
);
const seekMaximum = computed(() =>
	Math.max(0, Math.ceil(current.value?.duration_seconds ?? 0) - 1),
);
const timelinePosition = computed(() =>
	seekDraft.value && seekDraft.value.playbackId === player.snapshot?.playback_id
		? seekDraft.value.position
		: position.value,
);
const timelineFill = computed(
	() => `${Math.min(100, (timelinePosition.value / (seekMaximum.value || 1)) * 100)}%`,
);
function beginSeek() {
	seekTarget.value = seekEnabled.value ? (player.snapshot?.playback_id ?? null) : null;
}
function previewSeek(event: Event) {
	if (!seekTarget.value) beginSeek();
	if (seekTarget.value && seekTarget.value === player.snapshot?.playback_id)
		seekDraft.value = {
			position: (event.target as HTMLInputElement).valueAsNumber,
			playbackId: seekTarget.value,
		};
}
function seekKey(event: KeyboardEvent) {
	if (
		[
			"ArrowLeft",
			"ArrowRight",
			"ArrowUp",
			"ArrowDown",
			"Home",
			"End",
			"PageUp",
			"PageDown",
		].includes(event.key)
	)
		beginSeek();
	if (event.key === "Escape") seekDraft.value = null;
}
async function commitSeek() {
	const draft = seekDraft.value;
	if (draft && draft.playbackId === player.snapshot?.playback_id && seekEnabled.value)
		await player.seek(draft.position, draft.playbackId);
	seekDraft.value = null;
	seekTarget.value = null;
}
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
	<section class="player-dock" aria-label="Playback controls">
		<div class="dock-track flex min-w-0 items-center gap-3">
			<PlayerTrackArtwork :entry="current" class="dock-cover" />
			<div class="min-w-0">
				<NuxtLink
					to="/"
					class="block truncate text-sm font-semibold text-highlighted hover:underline"
					:title="current ? trackTitle(current) : undefined"
					>{{ current ? trackTitle(current) : "Nothing playing" }}</NuxtLink
				>
				<p class="mt-1 truncate text-xs text-muted">
					<PlayerArtistLink v-if="current" :entry="current" /><template v-else
						>Your next track is up to you</template
					>
				</p>
			</div>
		</div>
		<div class="dock-transport flex items-center justify-center gap-3">
			<UTooltip text="Stop and return track to queue">
				<UButton
					:icon="icons.stop"
					aria-label="Stop playback"
					color="neutral"
					variant="ghost"
					class="size-10 justify-center"
					:disabled="!player.enabled || !canControl(player.snapshot, 'stop')"
					@click="player.control('stop')"
				/>
			</UTooltip>
			<UButton
				:icon="action === 'pause' ? icons.pause : icons.play"
				:aria-label="label"
				size="xl"
				class="dock-play size-10 justify-center rounded-full"
				color="neutral"
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
					class="size-10 justify-center"
					:disabled="!player.enabled || !canControl(player.snapshot, 'skip')"
					@click="player.control('skip')"
				/>
			</UTooltip>
		</div>
		<div class="dock-timeline flex items-center gap-3 text-[0.6875rem] tabular-nums text-muted">
			<span class="w-9 text-right">{{ formatTime(timelinePosition) }}</span>
			<input
				v-if="current?.duration_seconds"
				type="range"
				class="seek-slider min-w-0 flex-1"
				min="0"
				:max="seekMaximum"
				step="1"
				:value="timelinePosition"
				:style="{ '--seek-progress': timelineFill }"
				:disabled="!seekEnabled"
				aria-label="Playback position"
				:aria-valuetext="`${formatTime(timelinePosition)} of ${formatTime(current.duration_seconds)}`"
				title="Seek for everyone in the channel"
				@pointerdown="beginSeek"
				@keydown="seekKey"
				@input="previewSeek"
				@change="commitSeek"
				@pointercancel="seekDraft = null"
				@blur="seekDraft = null"
			/>
			<div v-else class="h-1 min-w-0 flex-1 rounded-full bg-accented" />
			<span class="w-9">{{ formatTime(current?.duration_seconds ?? null) }}</span>
		</div>
		<div class="dock-volume flex items-center gap-3">
			<UPopover :ui="{ content: 'w-64 p-4' }">
				<UTooltip text="Volume for everyone in the channel">
					<UButton
						:icon="icons.volume"
						aria-label="Bot volume"
						color="neutral"
						variant="ghost"
						class="size-10 justify-center"
					/>
				</UTooltip>
				<template #content>
					<div class="mb-3 flex items-center justify-between gap-2 text-sm">
						<label for="bot-volume-popup">Bot volume</label
						><output for="bot-volume-popup" class="tabular-nums text-muted"
							>{{ volume }}%</output
						>
					</div>
					<input
						id="bot-volume-popup"
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
					<p class="mt-2 text-xs text-muted">For everyone in the channel.</p>
				</template>
			</UPopover>
			<label for="bot-volume" class="sr-only">Bot volume for everyone</label>
			<input
				id="bot-volume"
				v-model.number="volume"
				type="range"
				min="0"
				max="100"
				step="1"
				class="volume-slider hidden min-w-0 w-24 lg:block"
				:disabled="!player.enabled"
				:aria-valuetext="`${volume} percent`"
				@change="setVolume"
			/>
			<output
				for="bot-volume"
				class="hidden w-9 text-right text-xs tabular-nums text-muted lg:block"
				>{{ volume }}%</output
			>
		</div>
	</section>
</template>

<style scoped>
.player-dock {
	position: relative;
	display: grid;
	grid-template-columns: minmax(9rem, 1fr) auto minmax(10rem, 1.3fr) auto;
	grid-template-areas: "track transport timeline volume";
	align-items: center;
	gap: 1.5rem;
	flex-shrink: 0;
	min-height: 5.5rem;
	padding: 1rem 1.75rem max(1rem, env(safe-area-inset-bottom));
	background: var(--room-dock);
	border-top: 1px solid var(--ui-border);
}
.dock-track {
	grid-area: track;
}
.dock-transport {
	grid-area: transport;
	gap: 0.375rem;
}
.dock-timeline {
	grid-area: timeline;
	gap: 0.5rem;
}
.dock-volume {
	grid-area: volume;
	justify-self: end;
	gap: 0.25rem;
}
.dock-cover {
	width: 2.75rem;
	height: 2.75rem;
	border-radius: 0.375rem;
}
.dock-play {
	transition: background-color 160ms cubic-bezier(0.16, 1, 0.3, 1);
}
.seek-slider {
	height: 1.75rem;
	appearance: none;
	background: transparent;
	cursor: pointer;
}
.seek-slider::-webkit-slider-runnable-track {
	height: 3px;
	border-radius: 999px;
	background: linear-gradient(
		to right,
		var(--ui-primary) var(--seek-progress),
		var(--ui-bg-accented) var(--seek-progress)
	);
}
.seek-slider::-moz-range-track {
	height: 3px;
	border-radius: 999px;
	background: var(--ui-bg-accented);
}
.seek-slider::-moz-range-progress {
	height: 3px;
	border-radius: 999px;
	background: var(--ui-primary);
}
.seek-slider::-webkit-slider-thumb {
	appearance: none;
	width: 0.75rem;
	height: 0.75rem;
	margin-top: -4.5px;
	border-radius: 50%;
	background: var(--ui-primary);
}
.seek-slider::-moz-range-thumb {
	width: 0.75rem;
	height: 0.75rem;
	border: 0;
	border-radius: 50%;
	background: var(--ui-primary);
}
.seek-slider:disabled {
	cursor: default;
	opacity: 0.5;
}
.volume-slider {
	accent-color: var(--ui-primary);
	min-height: 24px;
	cursor: pointer;
}
.volume-slider:disabled {
	cursor: default;
	opacity: 0.5;
}
@container workspace (max-width: 1000px) {
	.player-dock {
		grid-template-columns: minmax(0, 1fr) auto auto;
		grid-template-areas: "track transport volume" "timeline timeline timeline";
		gap: 0.75rem 1rem;
		padding: 0.75rem 1.5rem;
	}
	.dock-volume input,
	.dock-volume > output {
		display: none;
	}
}
@container workspace (max-width: 600px) {
	.player-dock {
		gap: 0.75rem;
		padding: 0.75rem 1rem max(0.75rem, env(safe-area-inset-bottom));
	}
	.dock-cover {
		display: none;
	}
	.dock-track p {
		font-size: 0.6875rem;
	}
	.dock-transport {
		gap: 0;
	}
	.dock-volume > button {
		width: 2rem;
	}
}
@media (prefers-reduced-motion: reduce) {
	.dock-play {
		transition: none;
	}
}
</style>
