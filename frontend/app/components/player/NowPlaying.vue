<script setup lang="ts">
import { trackArtwork, trackTitle, youtubeVideoId } from "#shared/player";
import { usePlayerStore } from "~/stores/player";

const props = defineProps<{ active: boolean }>();
const emit = defineEmits<{ queue: [] }>();
const player = usePlayerStore();
const { icons } = useTheme();
const { currentPosition } = usePlaybackPosition();
const current = computed(() => player.snapshot?.current ?? null);
const nextTrack = computed(() => player.snapshot?.upcoming[0] ?? null);
const videoId = computed(() => (current.value ? youtubeVideoId(current.value.source_url) : null));
const artwork = computed(() => trackArtwork(current.value));
const preview = ref<"cover" | "video">("cover");
const videoControls = ref(false);
const visibility = useDocumentVisibility();
const videoFailed = ref(false);
const artworkFailed = ref(false);
const videoReady = ref(false);
const videoBlocked = ref(false);
const video = useTemplateRef<{ align: () => void; startPreview: () => void }>("video");
const loadVideo = computed(
	() => props.active && visibility.value === "visible" && player.connection === "live",
);
watch(artwork, () => {
	artworkFailed.value = false;
});
watch(
	() => player.snapshot?.playback_id,
	() => {
		videoFailed.value = false;
		videoReady.value = false;
		videoBlocked.value = false;
		videoControls.value = false;
	},
);
function showVideo() {
	preview.value = "video";
	videoFailed.value = false;
}
watch(preview, () => {
	videoControls.value = false;
});
watch(loadVideo, (visible) => {
	if (!visible) videoControls.value = false;
});
</script>

<template>
	<section
		class="listening-view"
		aria-label="Now playing"
		:class="{
			'is-video': preview === 'video' && !videoFailed,
			'has-video-controls': preview === 'video' && videoControls && !videoFailed,
		}"
	>
		<div class="media-stage">
			<img
				v-if="artwork && !artworkFailed"
				:src="artwork"
				alt=""
				referrerpolicy="no-referrer"
				class="media-backdrop"
				@error="artworkFailed = true"
			/>
			<div v-else class="media-placeholder" aria-hidden="true">
				<UIcon :name="icons.headphones" />
			</div>
			<PlayerVideo
				v-if="preview === 'video' && current && !videoFailed && videoId"
				ref="video"
				:key="player.snapshot?.playback_id ?? videoId"
				:video-id="videoId"
				:title="trackTitle(current)"
				:get-position="currentPosition"
				:active="loadVideo"
				:state="player.snapshot?.state ?? 'idle'"
				:interactive="videoControls"
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
			<div v-if="preview === 'video'" class="flex items-center gap-2">
				<button
					v-if="!videoFailed"
					type="button"
					class="media-tool-button"
					:aria-label="videoControls ? 'Hide YouTube controls' : 'Show YouTube controls'"
					:title="
						videoControls ? 'Hide YouTube controls' : 'YouTube controls and quality'
					"
					:aria-pressed="videoControls"
					:disabled="!videoReady || !loadVideo"
					@click="videoControls = !videoControls"
				>
					<UIcon :name="icons.settings" />
				</button>
				<button
					v-if="!videoFailed && !videoBlocked"
					type="button"
					class="media-tool-button"
					aria-label="Sync video"
					title="Sync video"
					:disabled="!videoReady || !loadVideo"
					@click="video?.align()"
				>
					<UIcon :name="icons.reload" />
				</button>
				<UPopover>
					<button
						type="button"
						class="media-tool-button"
						aria-label="About video preview"
						title="About video preview"
					>
						<UIcon :name="icons.info" />
					</button>
					<template #content
						><p class="max-w-64 p-4 text-sm text-default">
							Video starts muted. YouTube controls only change your preview. Sync
							returns it to the bot's position.
						</p></template
					>
				</UPopover>
			</div>
		</div>
		<div v-show="!videoControls || videoFailed" class="media-details">
			<div class="media-copy">
				<PlayerContributor
					v-if="current?.added_by"
					:contributor="current.added_by"
					class="media-contributor"
				/>
				<h2
					class="media-title"
					:class="{ 'is-long': current && trackTitle(current).length > 48 }"
				>
					<a
						v-if="current"
						:href="current.source_url"
						:title="trackTitle(current)"
						target="_blank"
						rel="noopener noreferrer"
						>{{ trackTitle(current) }}</a
					>
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
			<button v-if="current" type="button" class="next-track-cue" @click="emit('queue')">
				<span class="next-track-label text-xs text-muted">{{
					nextTrack ? "Coming up" : "Keep it going"
				}}</span>
				<PlayerTrackArtwork v-if="nextTrack" :entry="nextTrack" class="size-12!" />
				<span v-else class="next-track-icon"><UIcon :name="icons.plus" /></span>
				<span class="min-w-0 text-left">
					<span class="line-clamp-2 text-sm font-medium text-highlighted">{{
						nextTrack ? trackTitle(nextTrack) : "Add the next track"
					}}</span>
					<span v-if="nextTrack" class="mt-1 block text-xs text-muted"
						>{{ player.snapshot?.upcoming.length ?? 0 }} in queue</span
					>
				</span>
				<UIcon :name="icons.arrowRight" class="size-4 shrink-0 text-muted" />
			</button>
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
			<a
				v-if="videoFailed"
				:href="current.source_url"
				target="_blank"
				rel="noopener noreferrer"
				class="hover:underline"
				>Open on YouTube</a
			>
			<button v-else-if="videoBlocked" type="button" @click="video?.startPreview()">
				<UIcon :name="icons.play" />Start preview
			</button>
		</div>
	</section>
</template>

<style scoped>
.listening-view {
	display: flex;
	flex-direction: column;
	flex: 1;
	min-width: 0;
	min-height: 28rem;
	color: #fff;
}
.media-stage {
	position: absolute;
	inset: 0;
	z-index: -1;
	overflow: hidden;
	container-type: size;
	background: #17191c;
	pointer-events: none;
}
.media-backdrop {
	position: absolute;
	inset: 0;
	width: 100%;
	height: 100%;
	object-fit: cover;
	object-position: center;
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
	color: #ffffff0d;
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
	background:
		linear-gradient(0deg, rgb(8 10 13 / 92%), rgb(8 10 13 / 40%) 34%, transparent 72%),
		linear-gradient(55deg, rgb(8 10 13 / 90%) 0%, rgb(8 10 13 / 62%) 30%, transparent 78%);
}
.media-top-shade {
	background: linear-gradient(
		180deg,
		rgb(8 10 13 / 78%),
		rgb(8 10 13 / 24%) 20%,
		transparent 40%
	);
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
	background: rgb(8 10 13 / 78%);
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
	color: #e4e4e7;
	cursor: pointer;
	border-radius: 0.375rem;
	transition:
		background-color 160ms cubic-bezier(0.16, 1, 0.3, 1),
		color 160ms cubic-bezier(0.16, 1, 0.3, 1);
}
.media-preview-switch button[aria-pressed="true"] {
	background: #ffffff20;
	color: #fff;
}
.media-preview-switch button:hover,
.media-tool-button:hover {
	background: #ffffff30;
	color: #fff;
}
.media-tool-button {
	background: rgb(8 10 13 / 78%);
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
	color: #d4d4d8;
	margin-top: 1.25rem;
}
.media-empty-action {
	display: inline-flex;
	align-items: center;
	gap: 0.5rem;
	background: #f4f4f5;
	color: #18181b;
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
	border-left: 1px solid rgb(255 255 255 / 18%);
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
	background: rgb(255 255 255 / 8%);
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
.video-status-row button:hover,
.video-status-row a:hover {
	color: #fff;
}
.video-status-row button:disabled,
.media-tool-button:disabled {
	opacity: 0.5;
	cursor: default;
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
		border-top: 1px solid rgb(255 255 255 / 12%);
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
		background: linear-gradient(
			0deg,
			rgb(8 10 13 / 96%),
			rgb(8 10 13 / 65%) 38%,
			rgb(8 10 13 / 25%) 70%,
			rgb(8 10 13 / 10%) 100%
		);
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
	.media-preview-switch button,
	.media-tool-button {
		transition: none;
	}
}
</style>
