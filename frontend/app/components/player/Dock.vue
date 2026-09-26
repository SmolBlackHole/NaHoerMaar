<script setup lang="ts">
import { formatTime } from "~/core/models/player";

const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const { icons } = useTheme();
const { position } = usePlaybackPosition();
const current = computed(() => player.state?.runtime.current ?? null);
const duration = computed(
	() => player.state?.runtime.duration_seconds ?? current.value?.track.duration_seconds ?? null,
);
const seekTarget = ref<string | null>(null);
const seekDraft = ref<{ position: number; playbackId: string } | null>(null);
const hoverPosition = ref<number | null>(null);
const keyboardPreview = ref(false);
const confirmation = ref<"skip" | "stop" | null>(null);
let lastSkipAt = 0;
const seekEnabled = computed(
	() =>
		player.canControl &&
		player.state?.runtime.voice.phase === "connected" &&
		!!player.state.runtime.attempt_id &&
		!!duration.value &&
		["playing", "paused"].includes(player.state.runtime.phase),
);
const seekMaximum = computed(() => Math.max(0, Math.ceil(duration.value ?? 0) - 1));
const timelinePosition = computed(() =>
	seekDraft.value && seekDraft.value.playbackId === player.state?.runtime.attempt_id
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
watch(() => player.state?.runtime.attempt_id, cancelSeek);
function beginSeek() {
	seekTarget.value = seekEnabled.value ? (player.state?.runtime.attempt_id ?? null) : null;
}
function previewSeek(event: Event) {
	if (!seekTarget.value) beginSeek();
	if (seekTarget.value && seekTarget.value === player.state?.runtime.attempt_id)
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
	if (draft && draft.playbackId === player.state?.runtime.attempt_id && seekEnabled.value)
		await player.seek(draft.position);
	seekDraft.value = null;
	seekTarget.value = null;
}
const action = computed(() =>
	["playing", "transitioning"].includes(player.state?.runtime.phase ?? "idle") ? "pause" : "play",
);
const label = computed(
	() =>
		({
			disabled: "Play",
			idle: "Play",
			starting: "Loading",
			playing: "Pause",
			paused: "Resume",
			transitioning: "Pause",
			failed: "Retry track",
		})[player.state?.runtime.phase ?? "idle"],
);
const volume = ref(100);
const browserVolume = useState<number>("browser-video-volume", () => 0);
watch(
	() => player.state?.volume,
	(value) => {
		if (value !== undefined) volume.value = Math.round(value * 100);
	},
	{ immediate: true },
);
async function setVolume() {
	await player.setVolume(volume.value / 100);
	volume.value = Math.round((player.state?.volume ?? 1) * 100);
}
const crossfade = ref(7);
watch(
	() => player.state?.crossfade_seconds,
	(value) => {
		crossfade.value = value ?? 7;
	},
	{ immediate: true },
);
async function setCrossfade(seconds: number) {
	crossfade.value = seconds;
	await player.setCrossfade(seconds);
	crossfade.value = player.state?.crossfade_seconds ?? 7;
}
function requestStop() {
	confirmation.value = "stop";
}
async function requestSkip() {
	const now = Date.now();
	if (now - lastSkipAt < 1000) {
		confirmation.value = "skip";
		lastSkipAt = 0;
		return;
	}
	lastSkipAt = now;
	await player.skip();
}
async function confirmControl() {
	const action = confirmation.value;
	confirmation.value = null;
	if (action === "skip") await player.skip();
	else if (action === "stop") await player.stop();
}
const confirmationTitle = computed(() =>
	confirmation.value === "stop" ? "Stop playback?" : "Skip another track?",
);
const confirmationDescription = computed(() =>
	confirmation.value === "stop"
		? "The current track stops for everyone and returns to the front of the queue."
		: "A track was skipped less than a second ago. Confirm before skipping the next one.",
);
</script>

<template>
	<section class="player-dock" aria-label="Playback controls">
		<div class="dock-track flex min-w-0 items-center gap-3">
			<PlayerTrackArtwork :entry="current?.track ?? null" class="dock-cover" />
			<div class="min-w-0 flex-1">
				<UTooltip :text="current ? `Open player: ${current.track.title}` : 'Open player'">
					<NuxtLink
						to="/"
						class="block truncate text-sm font-semibold text-highlighted hover:underline"
						>{{ current?.track.title ?? "Nothing playing" }}</NuxtLink
					>
				</UTooltip>
				<p class="mt-1 truncate text-xs text-muted">
					<PlayerArtistLink v-if="current" :entry="current.track" /><template v-else
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
					:loading="player.isPending('stop')"
					color="neutral"
					variant="ghost"
					class="size-10 justify-center"
					:disabled="!player.canControl || !current"
					@click="requestStop"
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
						player.state?.runtime.phase === 'starting' ||
						player.isPending('play') ||
						player.isPending('pause')
					"
					:disabled="!player.canControl || (action === 'pause' && !current)"
					@click="action === 'pause' ? player.pause() : player.play()"
				/>
			</UTooltip>
			<UTooltip text="Skip track">
				<UButton
					:icon="icons.skip"
					aria-label="Skip track"
					:loading="player.isPending('skip')"
					color="neutral"
					variant="ghost"
					class="size-10 justify-center"
					:disabled="!player.canControl || !current"
					@click="requestSkip"
				/>
			</UTooltip>
		</div>
		<div class="dock-timeline flex items-center gap-3 text-[0.6875rem] tabular-nums text-muted">
			<span class="w-9 text-right">{{ formatTime(timelinePosition) }}</span>
			<div
				v-if="duration"
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
					:aria-valuetext="`${formatTime(timelinePosition)} of ${formatTime(duration)}`"
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
			<span class="w-9">{{ formatTime(duration) }}</span>
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
						:disabled="!player.canControl"
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
									!player.canControl ||
									player.state?.crossfade_seconds === undefined ||
									player.isPending('crossfade')
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
								:disabled="!player.canControl || player.isPending('crossfade')"
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
			<UTooltip text="Browser video volume, only on this device">
				<input
					id="browser-volume"
					v-model.number="browserVolume"
					type="range"
					min="0"
					max="100"
					step="1"
					class="volume-slider hidden min-w-0 w-24 lg:block"
					:aria-valuetext="`${browserVolume} percent, only on this device`"
				/>
			</UTooltip>
			<output
				for="browser-volume"
				class="hidden w-9 text-right text-xs tabular-nums text-muted lg:block"
				>{{ browserVolume }}%</output
			>
		</div>
		<UModal
			:open="confirmation !== null"
			:ui="{ footer: 'justify-end' }"
			:title="confirmationTitle"
			:description="confirmationDescription"
			@update:open="!$event && (confirmation = null)"
		>
			<template #footer>
				<UButton
					label="Cancel"
					color="neutral"
					variant="outline"
					@click="confirmation = null"
				/>
				<UButton
					:label="confirmation === 'stop' ? 'Stop and return' : 'Skip track'"
					:color="confirmation === 'stop' ? 'error' : 'primary'"
					:disabled="!player.canControl"
					:loading="
						confirmation === 'stop'
							? player.isPending('stop')
							: player.isPending('skip')
					"
					@click="confirmControl"
				/>
			</template>
		</UModal>
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
