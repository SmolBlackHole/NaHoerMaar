<script setup lang="ts">
import { canControl, formatTime, trackTitle } from "#shared/player";
import { usePlayerStore } from "~/stores/player";

const player = usePlayerStore();
const { icons } = useTheme();
const { position } = usePlaybackPosition();
const current = computed(() => player.snapshot?.current ?? null);
const seekTarget = ref<string | null>(null);
const seekDraft = ref<{ position: number; playbackId: string } | null>(null);
const hoverPosition = ref<number | null>(null);
const keyboardPreview = ref(false);
const seekEnabled = computed(
	() =>
		player.enabled &&
		player.snapshot?.voice_state === "connected" &&
		!!player.snapshot.attempt_id &&
		!!current.value?.duration_seconds &&
		["playing", "paused"].includes(player.snapshot.state),
);
const seekMaximum = computed(() =>
	Math.max(0, Math.ceil(current.value?.duration_seconds ?? 0) - 1),
);
const timelinePosition = computed(() =>
	seekDraft.value && seekDraft.value.playbackId === player.snapshot?.attempt_id
		? seekDraft.value.position
		: position.value,
);
const timelineFill = computed(
	() => `${Math.min(100, (timelinePosition.value / (seekMaximum.value || 1)) * 100)}%`,
);
const seekPreview = computed(() => {
	if (!seekEnabled.value) return null;
	if (seekDraft.value || keyboardPreview.value) return timelinePosition.value;
	return hoverPosition.value;
});
function hoverSeek(event: PointerEvent) {
	if (!seekEnabled.value || event.pointerType === "touch") return;
	keyboardPreview.value = false;
	const slider = event.currentTarget as HTMLInputElement;
	const { left, width } = slider.getBoundingClientRect();
	const thumb = parseFloat(getComputedStyle(slider).getPropertyValue("--seek-thumb-size"));
	const fraction = Math.max(0, Math.min(1, (event.clientX - left - thumb / 2) / (width - thumb)));
	hoverPosition.value = Math.round(fraction * seekMaximum.value);
}
function cancelSeek() {
	seekDraft.value = null;
	seekTarget.value = null;
	hoverPosition.value = null;
	keyboardPreview.value = false;
}
watch(() => player.snapshot?.attempt_id, cancelSeek);
function beginSeek() {
	seekTarget.value = seekEnabled.value ? (player.snapshot?.attempt_id ?? null) : null;
}
function previewSeek(event: Event) {
	if (!seekTarget.value) beginSeek();
	if (seekTarget.value && seekTarget.value === player.snapshot?.attempt_id)
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
	) {
		keyboardPreview.value = true;
		beginSeek();
	}
	if (event.key === "Escape") cancelSeek();
}
async function commitSeek() {
	const draft = seekDraft.value;
	if (draft && draft.playbackId === player.snapshot?.attempt_id && seekEnabled.value)
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
const browserVolume = useState<number>("browser-video-volume", () => 0);
watch(
	() => player.snapshot?.volume,
	(value) => {
		if (value !== undefined) volume.value = Math.round(value * 100);
	},
	{ immediate: true },
);
async function setVolume() {
	await player.setVolume(volume.value / 100);
	volume.value = Math.round((player.snapshot?.volume ?? 1) * 100);
}
const crossfade = ref(7);
watch(
	() => player.snapshot?.crossfade_seconds,
	(value) => {
		crossfade.value = value ?? 7;
	},
	{ immediate: true },
);
async function setCrossfade(seconds: number) {
	crossfade.value = seconds;
	await player.setCrossfade(seconds as Parameters<typeof player.setCrossfade>[0]);
	crossfade.value = player.snapshot?.crossfade_seconds ?? 7;
}
</script>

<template>
	<section class="player-dock" aria-label="Playback controls">
		<div class="dock-track flex min-w-0 items-center gap-3">
			<PlayerTrackArtwork :entry="current" class="dock-cover" />
			<div class="min-w-0 flex-1">
				<UTooltip :text="current ? `Open player: ${trackTitle(current)}` : 'Open player'">
					<NuxtLink
						to="/"
						class="block truncate text-sm font-semibold text-highlighted hover:underline"
						>{{ current ? trackTitle(current) : "Nothing playing" }}</NuxtLink
					>
				</UTooltip>
				<p class="mt-1 truncate text-xs text-muted">
					<PlayerArtistLink v-if="current" :entry="current" /><template v-else
						>Your next track is up to you</template
					>
				</p>
			</div>
			<PlayerRadioAction v-if="current" :entry="current" labelled />
		</div>
		<div class="dock-transport flex items-center justify-center gap-3">
			<UTooltip text="Stop and return track to queue">
				<UButton
					:icon="icons.stop"
					aria-label="Stop playback"
					:loading="player.isControlPending('stop')"
					color="neutral"
					variant="ghost"
					class="size-10 justify-center"
					:disabled="!player.enabled || !canControl(player.snapshot, 'stop')"
					@click="player.control('stop')"
				/>
			</UTooltip>
			<UTooltip :text="label">
				<UButton
					:icon="action === 'pause' ? icons.pause : icons.play"
					:aria-label="label"
					size="xl"
					class="dock-play size-10 justify-center rounded-full"
					color="neutral"
					:loading="
						player.snapshot?.state === 'loading' ||
						player.isControlPending('play') ||
						player.isControlPending('pause')
					"
					:disabled="!player.enabled || !canControl(player.snapshot, action)"
					@click="player.control(action)"
				/>
			</UTooltip>
			<UTooltip text="Skip track">
				<UButton
					:icon="icons.skip"
					aria-label="Skip track"
					:loading="player.isControlPending('skip')"
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
			<div
				v-if="current?.duration_seconds"
				class="seek-control min-w-0 flex-1"
				:style="{ '--seek-preview': (seekPreview ?? 0) / (seekMaximum || 1) }"
			>
				<span v-if="seekPreview !== null" class="seek-preview" aria-hidden="true">{{
					formatTime(seekPreview)
				}}</span>
				<input
					type="range"
					class="seek-slider"
					min="0"
					:max="seekMaximum"
					step="1"
					:value="timelinePosition"
					:style="{ '--seek-progress': timelineFill }"
					:disabled="!seekEnabled"
					aria-label="Playback position"
					:aria-valuetext="`${formatTime(timelinePosition)} of ${formatTime(current.duration_seconds)}`"
					aria-description="Seek for everyone in the channel"
					@pointerenter="hoverSeek"
					@pointermove="hoverSeek"
					@pointerleave="hoverPosition = null"
					@pointerdown="
						keyboardPreview = false;
						beginSeek();
						hoverSeek($event);
					"
					@focus="
						keyboardPreview = ($event.target as HTMLInputElement).matches(
							':focus-visible',
						)
					"
					@keydown="seekKey"
					@input="previewSeek"
					@change="commitSeek"
					@pointercancel="cancelSeek"
					@blur="cancelSeek"
				/>
			</div>
			<div v-else class="h-1 min-w-0 flex-1 rounded-full bg-accented" />
			<span class="w-9">{{ formatTime(current?.duration_seconds ?? null) }}</span>
		</div>
		<div class="dock-volume flex items-center gap-3">
			<UPopover :ui="{ content: 'w-72 max-w-[calc(100vw-2rem)] p-4' }">
				<UTooltip text="Audio settings: bot volume, video volume and crossfade">
					<UButton
						:icon="icons.volume"
						aria-label="Audio settings"
						color="neutral"
						variant="ghost"
						class="size-10 justify-center"
					/>
				</UTooltip>
				<template #content>
					<div class="mb-3 flex items-center justify-between gap-2 text-sm">
						<label for="bot-volume-popup">Discord bot volume</label
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
					<div class="mt-5 mb-3 flex items-center justify-between gap-2 text-sm">
						<label for="browser-volume-popup">Browser video volume</label>
						<output for="browser-volume-popup" class="tabular-nums text-muted"
							>{{ browserVolume }}%</output
						>
					</div>
					<input
						id="browser-volume-popup"
						v-model.number="browserVolume"
						type="range"
						min="0"
						max="100"
						step="1"
						class="volume-slider w-full"
						:aria-valuetext="`${browserVolume} percent, only on this device`"
					/>
					<p class="mt-1 text-xs text-muted">Only your video preview. Starts muted.</p>
					<div class="mt-5 space-y-3">
						<div class="flex items-center justify-between gap-3">
							<span id="crossfade-label" class="text-sm">Crossfade</span>
							<USwitch
								:model-value="crossfade > 0"
								aria-labelledby="crossfade-label"
								:disabled="
									!player.enabled ||
									player.snapshot?.crossfade_seconds === undefined ||
									player.isPending('playback.crossfade')
								"
								@update:model-value="setCrossfade($event ? 7 : 0)"
							/>
						</div>
						<div v-if="crossfade > 0" class="flex items-center gap-3">
							<input
								v-model.number="crossfade"
								type="range"
								min="3"
								max="7"
								step="1"
								class="volume-slider min-w-0 flex-1"
								aria-label="Crossfade duration"
								:aria-valuetext="`${crossfade} seconds`"
								:disabled="
									!player.enabled || player.isPending('playback.crossfade')
								"
								@change="setCrossfade(crossfade)"
							/>
							<output class="w-7 text-right text-sm tabular-nums text-muted"
								>{{ crossfade }}s</output
							>
						</div>
						<p class="text-xs leading-relaxed text-muted">
							{{
								crossfade
									? "Songs overlap at the next natural transition."
									: "Songs play one after another."
							}}
						</p>
					</div>
					<p class="mt-4 text-xs text-muted">
						Bot volume and crossfade affect everyone in the channel.
					</p>
				</template>
			</UPopover>
			<label for="browser-volume" class="sr-only"
				>Browser video volume, only on this device</label
			>
			<input
				id="browser-volume"
				v-model.number="browserVolume"
				type="range"
				min="0"
				max="100"
				step="1"
				class="volume-slider hidden min-w-0 w-24 lg:block"
				:aria-valuetext="`${browserVolume} percent, only on this device`"
				title="Browser video volume, only on this device"
			/>
			<output
				for="browser-volume"
				class="hidden w-9 text-right text-xs tabular-nums text-muted lg:block"
				>{{ browserVolume }}%</output
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
	transition:
		background-color 140ms ease-out,
		transform 100ms ease-out;
}
.dock-play:active:not(:disabled) {
	transform: scale(0.94);
}
.seek-control {
	--seek-thumb-size: 12px;
	position: relative;
}
.seek-preview {
	position: absolute;
	bottom: calc(100% + 0.25rem);
	left: clamp(
		1.75rem,
		calc(var(--seek-preview) * (100% - var(--seek-thumb-size)) + var(--seek-thumb-size) / 2),
		calc(100% - 1.75rem)
	);
	transform: translateX(-50%);
	z-index: 1;
	padding: 0.25rem 0.5rem;
	border: 1px solid var(--ui-border-accented);
	border-radius: 0.375rem;
	background: var(--ui-bg-elevated);
	color: var(--ui-text-highlighted);
	font-size: 0.75rem;
	font-weight: 500;
	line-height: 1.25rem;
	white-space: nowrap;
	pointer-events: none;
}
.seek-slider {
	display: block;
	width: 100%;
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
	width: var(--seek-thumb-size);
	height: var(--seek-thumb-size);
	margin-top: -4.5px;
	border-radius: 50%;
	background: var(--ui-primary);
}
.seek-slider::-moz-range-thumb {
	width: var(--seek-thumb-size);
	height: var(--seek-thumb-size);
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
	.dock-play:active:not(:disabled) {
		transform: none;
	}
	.dock-play {
		transition: none;
	}
}
</style>
