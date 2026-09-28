<script setup lang="ts">
import { trackSource, youtubeVideoId } from "~/core/models/player";
import { ArtworkHandoff } from "~/utils/artworkHandoff";
import { loadArtworkCrop, type ArtworkCrop } from "~/utils/artworkCrop";

const props = defineProps<{ active: boolean }>();
const emit = defineEmits<{ queue: [] }>();
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const consent = useConsentStore();
const { icons } = useTheme();
const { currentPosition } = usePlaybackPosition();
const current = computed(() => player.state?.runtime.current ?? null);
const loading = computed(() => !player.state && player.connection === "connecting");
const nextTrack = computed(() => player.state?.queue[0]?.request ?? null);
const source = computed(() => (current.value ? trackSource(current.value) : undefined));
const videoId = computed(() => (current.value ? youtubeVideoId(current.value) : null));
const artwork = computed(() =>
	consent.youtube ? (current.value?.track.artwork_url ?? null) : null,
);
const nextArtwork = computed(() =>
	consent.youtube ? (nextTrack.value?.track.artwork_url ?? null) : null,
);
const preview = ref<"cover" | "video">("cover");
const videoControls = ref(false);
const browserVolume = useState<number>("browser-video-volume", () => 0);
const visibility = useDocumentVisibility();
const reducedMotion = usePreferredReducedMotion();
const motionPaused = ref(false);
const artworkCrop = shallowRef<ArtworkCrop | null>(null);
const displayedArtwork = ref<string | null>(null);
const videoFailed = ref(false);
const videoReady = ref(false);
const videoBlocked = ref(false);
const video = useTemplateRef<{
	align: () => void;
	startPreview: () => void;
	retry: () => void;
}>("video");
const loadVideo = computed(
	() => props.active && visibility.value === "visible" && player.connection === "live",
);
let artworkHandoff: ArtworkHandoff | undefined;
let pendingArtworkCrop: { url: string; crop: ArtworkCrop | null } | null = null;
onMounted(() => {
	artworkHandoff = new ArtworkHandoff();
	watch(
		[artwork, nextArtwork],
		async ([currentUrl, nextUrl]) => {
			const result = await artworkHandoff?.update(currentUrl, nextUrl);
			if (!result || result.status === "stale") return;
			if (result.status !== "ready") {
				displayedArtwork.value = null;
				artworkCrop.value = null;
				return;
			}
			displayedArtwork.value = result.url;
			artworkCrop.value =
				pendingArtworkCrop?.url === result.url ? pendingArtworkCrop.crop : null;
		},
		{ immediate: true },
	);
	watch(
		artwork,
		async (url, _previous, onCleanup) => {
			pendingArtworkCrop = null;
			if (!url) {
				if (!displayedArtwork.value) artworkCrop.value = null;
				return;
			}
			const controller = new AbortController();
			onCleanup(() => controller.abort());
			const crop = await loadArtworkCrop(url, controller.signal);
			if (controller.signal.aborted) return;
			pendingArtworkCrop = { url, crop };
			if (displayedArtwork.value === url) artworkCrop.value = crop;
		},
		{ immediate: true, flush: "sync" },
	);
});
onBeforeUnmount(() => artworkHandoff?.dispose());
const videoVisible = computed(
	() => preview.value === "video" && videoReady.value && !videoFailed.value,
);
const coverVisible = computed(() => !videoVisible.value);
const coverMoving = computed(
	() =>
		props.active &&
		visibility.value === "visible" &&
		coverVisible.value &&
		["starting", "playing", "transitioning"].includes(player.state?.runtime.phase ?? "idle") &&
		!motionPaused.value &&
		reducedMotion.value !== "reduce",
);
watch(
	() => player.state?.runtime.playback_id,
	() => {
		videoFailed.value = false;
		videoReady.value = false;
		videoBlocked.value = false;
		videoControls.value = false;
	},
);
function showVideo() {
	if (!consent.youtube) {
		consent.open = true;
		return;
	}
	preview.value = "video";
	if (videoFailed.value) retryVideo();
}
function retryVideo() {
	videoFailed.value = false;
	videoReady.value = false;
	videoBlocked.value = false;
	void nextTick(() => video.value?.retry());
}
watch(preview, () => {
	videoControls.value = false;
});
watch(
	() => consent.youtube,
	(allowed) => {
		if (!allowed) {
			preview.value = "cover";
			videoReady.value = false;
		}
	},
);
watch(loadVideo, (visible) => {
	if (!visible) videoControls.value = false;
});
</script>

<template>
	<section
		class="listening-view"
		aria-label="Now playing"
		:class="{
			'is-video': videoVisible,
			'has-video-controls': videoVisible && videoControls,
		}"
	>
		<div class="media-stage">
			<div v-if="loading" class="media-loading" aria-hidden="true">
				<USkeleton class="size-full rounded-none" />
			</div>
			<div
				v-else-if="displayedArtwork"
				:key="displayedArtwork"
				class="media-artwork"
				:class="{ 'is-moving': coverMoving }"
			>
				<img
					:src="displayedArtwork"
					alt=""
					referrerpolicy="no-referrer"
					class="media-backdrop"
					:class="{ 'is-cropped': artworkCrop }"
					:style="
						artworkCrop
							? {
									'--artwork-ratio': artworkCrop.ratio,
									'--artwork-width': artworkCrop.width,
									'--artwork-height': artworkCrop.height,
								}
							: undefined
					"
				/>
			</div>
			<div v-else class="media-placeholder" aria-hidden="true">
				<UIcon :name="icons.headphones" />
			</div>
			<PlayerVideo
				v-if="consent.youtube && current && videoId"
				ref="video"
				:key="player.state?.runtime.playback_id ?? videoId"
				:video-id="videoId"
				:title="current.track.title"
				:get-position="currentPosition"
				:active="loadVideo"
				:visible="videoVisible"
				:state="player.state?.runtime.phase ?? 'idle'"
				:interactive="videoControls"
				:volume="browserVolume"
				@ready="videoReady = $event"
				@blocked="videoBlocked = $event"
				@failed="videoFailed = true"
				@ended="preview = 'cover'"
			/>
			<template v-if="!videoControls || videoFailed">
				<div class="media-blur" aria-hidden="true" />
				<div class="media-shade" aria-hidden="true" />
				<div class="media-top-shade" aria-hidden="true" />
			</template>
		</div>
		<div v-if="current" class="media-toolbar">
			<div class="media-preview-switch" role="group" aria-label="Preview mode">
				<button
					type="button"
					:aria-pressed="preview === 'cover'"
					@click="
						preview = 'cover';
						videoFailed = false;
					"
				>
					Cover
				</button>
				<button
					v-if="videoId"
					type="button"
					:aria-pressed="preview === 'video'"
					@click="showVideo"
				>
					Video
				</button>
			</div>
			<button
				v-if="coverVisible && displayedArtwork && reducedMotion !== 'reduce'"
				type="button"
				class="media-tool-button"
				:aria-label="motionPaused ? 'Resume cover motion' : 'Pause cover motion'"
				@click="motionPaused = !motionPaused"
			>
				<UIcon :name="motionPaused ? icons.play : icons.pause" />
				{{ motionPaused ? "Resume motion" : "Pause motion" }}
			</button>
			<button
				v-if="!consent.youtube"
				type="button"
				class="media-tool-button"
				@click="consent.open = true"
			>
				Enable covers &amp; video
			</button>
			<div v-if="preview === 'video'" class="flex items-center gap-2">
				<UTooltip
					v-if="!videoFailed"
					:text="videoControls ? 'Hide YouTube controls' : 'YouTube controls and quality'"
				>
					<button
						type="button"
						class="media-tool-button"
						:aria-label="
							videoControls ? 'Hide YouTube controls' : 'Show YouTube controls'
						"
						:aria-pressed="videoControls"
						:disabled="!videoReady || !loadVideo"
						@click="videoControls = !videoControls"
					>
						<UIcon :name="icons.settings" />
					</button>
				</UTooltip>
				<UTooltip v-if="!videoFailed && !videoBlocked" text="Sync video">
					<button
						type="button"
						class="media-tool-button"
						aria-label="Sync video"
						:disabled="!videoReady || !loadVideo"
						@click="video?.align()"
					>
						<UIcon :name="icons.reload" />
					</button>
				</UTooltip>
				<UPopover>
					<UTooltip text="About video preview">
						<button
							type="button"
							class="media-tool-button"
							aria-label="About video preview"
						>
							<UIcon :name="icons.info" />
						</button>
					</UTooltip>
					<template #content
						><p class="max-w-64 p-4 text-sm text-default">
							Video starts muted. Browser video volume and YouTube controls affect
							only your preview. Sync returns it to the bot's position.
						</p></template
					>
				</UPopover>
			</div>
		</div>
		<div v-show="!videoControls || videoFailed" class="media-details">
			<div v-if="loading" class="media-copy space-y-4" aria-busy="true">
				<USkeleton class="h-5 w-36 rounded-full" />
				<USkeleton class="h-12 w-full max-w-2xl" />
				<USkeleton class="h-12 w-3/5 max-w-lg" />
				<USkeleton class="mt-3 h-10 w-32 rounded-lg" />
			</div>
			<div v-else class="media-copy">
				<PlayerContributor
					v-if="current"
					:contributor="current.contributor"
					:origin="current.origin"
					class="media-contributor"
				/>
				<h2
					class="media-title"
					:class="{ 'is-long': current && current.track.title.length > 48 }"
				>
					<UTooltip v-if="current" :text="`Open source: ${current.track.title}`">
						<a :href="source?.source_url" target="_blank" rel="noopener noreferrer">{{
							current.track.title
						}}</a>
					</UTooltip>
					<template v-else>What are we<br />listening to?</template>
				</h2>
				<p v-if="!current" class="media-empty-help">
					Add something to the queue.<br />The room is yours.
				</p>
				<button
					v-if="!current"
					type="button"
					class="media-empty-action"
					@click="emit('queue')"
				>
					<UIcon :name="icons.plus" />{{ nextTrack ? "Open queue" : "Add a track" }}
				</button>
			</div>
			<div v-if="loading" class="next-track-cue" aria-hidden="true">
				<USkeleton class="size-12! shrink-0 rounded-lg" />
				<div class="min-w-0 flex-1 space-y-2">
					<USkeleton class="h-4 w-full" />
					<USkeleton class="h-3 w-16" />
				</div>
			</div>
			<UTooltip
				v-else-if="current"
				:text="
					nextTrack ? `Open queue: ${nextTrack.track.title}` : 'Open queue to add a track'
				"
			>
				<button type="button" class="next-track-cue" @click="emit('queue')">
					<span class="next-track-label text-xs text-muted">{{
						nextTrack ? "Coming up" : "Keep it going"
					}}</span>
					<PlayerTrackArtwork
						v-if="nextTrack"
						:entry="nextTrack.track"
						class="size-12!"
					/>
					<span v-else class="next-track-icon"><UIcon :name="icons.plus" /></span>
					<span class="min-w-0 text-left">
						<span class="line-clamp-2 text-sm font-medium text-highlighted">{{
							nextTrack ? nextTrack.track.title : "Add the next track"
						}}</span>
						<span v-if="nextTrack" class="mt-1 block text-xs text-muted"
							>{{ player.state?.queue.length ?? 0 }} in queue</span
						>
					</span>
					<UIcon :name="icons.arrowRight" class="size-4 shrink-0 text-muted" />
				</button>
			</UTooltip>
		</div>
		<div
			v-if="
				preview === 'video' &&
				current &&
				(videoFailed || videoBlocked || (loadVideo && !videoReady))
			"
			class="video-status-row"
		>
			<p role="status">
				{{
					videoFailed
						? "This video can't be embedded."
						: videoBlocked
							? "Your browser paused the preview."
							: "Loading video…"
				}}
			</p>
			<div v-if="videoFailed" class="video-status-actions">
				<button type="button" @click="retryVideo">
					<UIcon :name="icons.reload" />Retry
				</button>
				<a :href="source?.source_url" target="_blank" rel="noopener noreferrer"
					>Open on YouTube</a
				>
			</div>
			<button v-else-if="videoBlocked" type="button" @click="video?.startPreview()">
				<UIcon :name="icons.play" />Start preview
			</button>
		</div>
	</section>
</template>

<style scoped>
.listening-view {
	--player-stage-bg: light-dark(var(--ui-bg), #17191c);
	--player-foreground: light-dark(#18181b, #fff);
	--player-placeholder: light-dark(rgb(9 9 11 / 6%), rgb(255 255 255 / 5%));
	--player-shade-bottom: linear-gradient(
		0deg,
		light-dark(rgb(250 250 250 / 92%), rgb(8 10 13 / 92%)),
		light-dark(rgb(250 250 250 / 62%), rgb(8 10 13 / 40%)) 34%,
		transparent 72%
	);
	--player-shade-side: linear-gradient(
		55deg,
		light-dark(rgb(250 250 250 / 72%), rgb(8 10 13 / 90%)) 0%,
		light-dark(rgb(250 250 250 / 42%), rgb(8 10 13 / 62%)) 30%,
		transparent 78%
	);
	--player-shade-top: linear-gradient(
		180deg,
		light-dark(rgb(250 250 250 / 60%), rgb(8 10 13 / 78%)),
		light-dark(rgb(250 250 250 / 20%), rgb(8 10 13 / 24%)) 20%,
		transparent 40%
	);
	--player-shade-mobile: linear-gradient(
		0deg,
		light-dark(rgb(250 250 250 / 96%), rgb(8 10 13 / 96%)),
		light-dark(rgb(250 250 250 / 72%), rgb(8 10 13 / 65%)) 38%,
		light-dark(rgb(250 250 250 / 30%), rgb(8 10 13 / 25%)) 70%,
		light-dark(rgb(250 250 250 / 12%), rgb(8 10 13 / 10%)) 100%
	);
	--player-control-bg: light-dark(rgb(255 255 255 / 82%), rgb(8 10 13 / 78%));
	--player-control-active: light-dark(rgb(9 9 11 / 10%), rgb(255 255 255 / 13%));
	--player-control-hover: light-dark(rgb(9 9 11 / 16%), rgb(255 255 255 / 19%));
	--player-divider: light-dark(rgb(9 9 11 / 16%), rgb(255 255 255 / 18%));
	--player-divider-subtle: light-dark(rgb(9 9 11 / 10%), rgb(255 255 255 / 12%));
	--player-icon-bg: light-dark(rgb(9 9 11 / 7%), rgb(255 255 255 / 8%));
	display: flex;
	flex-direction: column;
	flex: 1;
	min-width: 0;
	min-height: 28rem;
	color: var(--player-foreground);
}
.media-stage {
	position: absolute;
	inset: 0;
	z-index: -1;
	overflow: hidden;
	container-type: size;
	background: var(--player-stage-bg);
	pointer-events: none;
}
.media-loading {
	position: absolute;
	inset: 0;
}
.media-artwork {
	position: absolute;
	inset: 0;
	animation: cover-drift 60s ease-in-out infinite alternate;
	animation-play-state: paused;
}
.media-artwork.is-moving {
	animation-play-state: running;
}
@keyframes cover-drift {
	0% {
		transform: translate(-1.2%, 0.6%) scale(1.06);
	}
	45% {
		transform: translate(1.5%, -1.2%) scale(1.16);
	}
	100% {
		transform: translate(-0.6%, 1.2%) scale(1.09);
	}
}
.media-backdrop {
	position: absolute;
	inset: 0;
	width: 100%;
	height: 100%;
	object-fit: cover;
	object-position: center;
}
.media-backdrop.is-cropped {
	inset: auto;
	left: 50%;
	top: 50%;
	width: max(
		calc(100cqw / var(--artwork-width)),
		calc(100cqh * var(--artwork-ratio) / var(--artwork-height))
	);
	height: max(
		calc(100cqh / var(--artwork-height)),
		calc(100cqw / var(--artwork-ratio) / var(--artwork-width))
	);
	max-width: none;
	transform: translate(-50%, -50%);
}
.media-details {
	display: grid;
	grid-template-columns: minmax(0, 1fr) 18rem;
	align-items: end;
	gap: 3rem;
	margin-top: auto;
	padding-block: 6rem 1.5rem;
}
.has-video-controls .media-stage {
	top: 4rem;
	z-index: 1;
	pointer-events: auto;
}
.media-toolbar,
.video-status-row {
	position: relative;
	z-index: 2;
}
.media-placeholder {
	position: absolute;
	inset: 0;
	display: grid;
	place-items: center end;
	padding-right: 12%;
	color: var(--player-placeholder);
}
.media-placeholder > span {
	width: 14rem;
	height: 14rem;
}
.media-blur,
.media-shade,
.media-top-shade {
	position: absolute;
	inset: 0;
	pointer-events: none;
}
.media-blur {
	backdrop-filter: blur(24px);
	mask-image: linear-gradient(55deg, #000 0%, #000 18%, transparent 64%);
}
.media-shade {
	background: var(--player-shade-bottom), var(--player-shade-side);
}
.media-top-shade {
	background: var(--player-shade-top);
}
.media-toolbar {
	display: flex;
	flex-wrap: wrap;
	justify-content: space-between;
	align-items: center;
	gap: 1rem;
	flex-shrink: 0;
}
.media-preview-switch {
	display: inline-flex;
	background: var(--player-control-bg);
	border-radius: 0.5rem;
	padding: 0.25rem;
}
.media-preview-switch button,
.media-tool-button {
	display: inline-flex;
	align-items: center;
	justify-content: center;
	gap: 0.5rem;
	min-height: 2.25rem;
	padding: 0.375rem 0.75rem;
	font-size: 0.75rem;
	color: var(--player-foreground);
	cursor: pointer;
	border-radius: 0.375rem;
	transition:
		background-color 160ms cubic-bezier(0.16, 1, 0.3, 1),
		color 160ms cubic-bezier(0.16, 1, 0.3, 1);
}
.media-preview-switch button[aria-pressed="true"] {
	background: var(--player-control-active);
	color: var(--player-foreground);
}
.media-preview-switch button:hover,
.media-tool-button:hover {
	background: var(--player-control-hover);
	color: var(--player-foreground);
}
.media-tool-button {
	background: var(--player-control-bg);
}
.media-tool-button > span {
	width: 1rem;
	height: 1rem;
}
.media-copy {
	min-width: 0;
}
.media-title {
	max-width: 26ch;
	font-size: 3.25rem;
	font-weight: 600;
	line-height: 1.09;
	letter-spacing: -0.035em;
	text-wrap: balance;
	overflow-wrap: anywhere;
}
.media-title a:hover {
	text-decoration: underline;
	text-decoration-thickness: 2px;
}
.media-title.is-long {
	font-size: 2.75rem;
}
.media-contributor {
	margin-bottom: 1rem;
	font-size: 0.75rem;
	color: var(--ui-text-muted);
}
.media-empty-help {
	font-size: 1rem;
	line-height: 1.65;
	color: var(--ui-text-muted);
	margin-top: 1.25rem;
}
.media-empty-action {
	display: inline-flex;
	align-items: center;
	gap: 0.5rem;
	background: var(--player-foreground);
	color: var(--player-stage-bg);
	padding: 0.75rem 1rem;
	border-radius: 0.5rem;
	margin-top: 1.5rem;
	font-size: 0.875rem;
	font-weight: 500;
	cursor: pointer;
}
.next-track-cue {
	display: grid;
	grid-template-columns: 3rem minmax(0, 1fr) 1rem;
	align-items: center;
	gap: 0.75rem 1rem;
	width: 100%;
	min-width: 0;
	padding: 0.25rem 0 0.25rem 2rem;
	border-left: 1px solid var(--player-divider);
	text-align: left;
	cursor: pointer;
}
.next-track-label {
	grid-column: 1 / -1;
}
.next-track-cue:hover .text-highlighted {
	color: var(--ui-primary);
}
.next-track-icon {
	display: grid;
	place-items: center;
	width: 3rem;
	height: 3rem;
	flex-shrink: 0;
	border-radius: 0.5rem;
	background: var(--player-icon-bg);
	color: var(--ui-text-muted);
}
.next-track-icon > span {
	width: 1rem;
	height: 1rem;
}
.video-status-row {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: space-between;
	gap: 0.5rem;
	min-height: 2.5rem;
	width: min(100%, 48rem);
	color: var(--ui-text-muted);
	font-size: 0.75rem;
}
.video-status-row button {
	display: flex;
	align-items: center;
	gap: 0.5rem;
	min-height: 2.25rem;
	cursor: pointer;
}
.video-status-actions {
	display: flex;
	align-items: center;
	gap: 1rem;
}
.video-status-row button:hover,
.video-status-row a:hover {
	color: var(--player-foreground);
}
.video-status-row button:disabled,
.media-tool-button:disabled {
	opacity: 0.5;
	cursor: default;
}
@container workspace (min-width: 901px) {
	.media-toolbar {
		margin-top: 0.75rem;
	}
	.media-details {
		padding-bottom: 0;
	}
}
@container workspace (max-width: 1000px) {
	.media-title {
		font-size: 2.75rem;
	}
}
@container workspace (max-width: 900px) {
	.media-details {
		grid-template-columns: minmax(0, 1fr);
		gap: 2rem;
	}
	.next-track-cue {
		max-width: 30rem;
		padding: 1.25rem 0 0;
		border-left: 0;
		border-top: 1px solid var(--player-divider-subtle);
	}
}
@container workspace (max-width: 600px) {
	.listening-view {
		min-height: 24rem;
	}
	.media-title {
		font-size: 2rem;
	}
	.media-title.is-long {
		font-size: 1.75rem;
		line-height: 1.2;
	}
	.media-details {
		gap: 1.5rem;
		padding-block: 7rem 0.5rem;
	}
	.media-toolbar {
		gap: 0.5rem;
	}
	.next-track-cue {
		gap: 0.75rem;
	}
	.media-preview-switch button,
	.media-tool-button {
		padding-inline: 0.625rem;
	}
	.media-shade {
		background: var(--player-shade-mobile);
	}
	.media-blur {
		mask-image: linear-gradient(0deg, #000, transparent 65%);
	}
	.media-placeholder {
		padding-right: 0;
		place-items: start center;
		padding-top: 4rem;
	}
	.media-placeholder > span {
		width: 10rem;
		height: 10rem;
	}
}
@media (prefers-reduced-motion: reduce) {
	.media-artwork {
		animation: none;
	}
	.media-preview-switch button,
	.media-tool-button {
		transition: none;
	}
}
</style>
