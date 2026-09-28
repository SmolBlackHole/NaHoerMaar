<script setup lang="ts">
import LyricsEditImpact from "./lyrics/edit/Impact.vue";
import LyricsEditOrbit from "./lyrics/edit/Orbit.vue";
import LyricsEditSpiral from "./lyrics/edit/Spiral.vue";
import { activeLyricLine, activeLyricWord, lyricPresentationEnd } from "~/core/models/lyrics";

const props = defineProps<{ trackId: string; visible: boolean }>();
const workflow = useNuxtApp().$backendCore.workflows.lyrics();
const { icons } = useTheme();
const { position } = usePlaybackPosition();
const reducedMotion = usePreferredReducedMotion();
const viewport = useTemplateRef<HTMLElement>("viewport");
const following = ref(true);
const { mode: preferredMode, editVariant, selectTrack, setSynchronized } = useLyricsDisplay();
const skeletonWidths = ["54%", "72%", "45%", "67%", "58%", "78%", "49%", "63%"];
const result = workflow.lyrics;
const lyrics = computed(() => result.data.value);
const mode = computed<LyricsMode>(() =>
	lyrics.value?.synchronized === false ? "read" : preferredMode.value,
);
const activeIndex = computed(() => {
	if (!lyrics.value?.synchronized) return null;
	return activeLyricLine(lyrics.value.lines, position.value);
});
const focusIndex = computed(() => activeIndex.value);
const focusLine = computed(() =>
	focusIndex.value === null ? null : (lyrics.value?.lines[focusIndex.value] ?? null),
);
const previousLine = computed(() => {
	if (focusIndex.value === null || focusIndex.value === 0) return null;
	return lyrics.value?.lines[focusIndex.value - 1] ?? null;
});
const nextLine = computed(() => {
	if (focusIndex.value === null) return null;
	return lyrics.value?.lines[focusIndex.value + 1] ?? null;
});
const focusWords = computed(() => focusLine.value?.text.trim().split(/\s+/).filter(Boolean) ?? []);
const activeWordIndex = computed(() => {
	if (activeIndex.value === null || !focusLine.value) return null;
	return activeLyricWord(focusLine.value, position.value, nextLine.value?.start_seconds ?? null);
});
const editSequence = computed(() => {
	const lines = lyrics.value?.lines;
	const currentLineIndex = activeIndex.value;
	if (!lines || currentLineIndex === null)
		return { tokens: [], contextTokens: [], verseLines: [], activeWordIndex: 0 };

	const isContinuous = (leftIndex: number) => {
		const previous = lines[leftIndex];
		const current = lines[leftIndex + 1];
		if (!previous || !current || current.start_seconds === null) return false;
		const previousEnd = lyricPresentationEnd(previous, current.start_seconds);
		return previousEnd !== null && Math.abs(previousEnd - current.start_seconds) <= 0.001;
	};

	let segmentStart = currentLineIndex;
	while (segmentStart > 0 && isContinuous(segmentStart - 1)) segmentStart--;
	let segmentEnd = currentLineIndex;
	while (segmentEnd < lines.length - 1 && isContinuous(segmentEnd)) segmentEnd++;

	const tokenize = (startIndex: number, endIndex: number) =>
		lines.slice(startIndex, endIndex + 1).flatMap((line, offset) => {
			const lineIndex = startIndex + offset;
			return line.text
				.trim()
				.split(/\s+/)
				.filter(Boolean)
				.map((text, wordIndex) => ({
					id: `${lineIndex}-${wordIndex}-${line.start_seconds ?? "plain"}`,
					text,
				}));
		});
	const tokens = tokenize(segmentStart, currentLineIndex);
	const contextTokens = tokenize(segmentStart, segmentEnd);
	const precedingCount = lines
		.slice(segmentStart, currentLineIndex)
		.reduce((total, line) => total + line.text.trim().split(/\s+/).filter(Boolean).length, 0);
	const active = precedingCount + Math.max(0, activeWordIndex.value ?? 0);
	return {
		tokens,
		contextTokens,
		verseLines: lines.slice(segmentStart, segmentEnd + 1).map((line, offset) => ({
			id: segmentStart + offset,
			text: line.text,
		})),
		activeWordIndex: active,
	};
});
const presentationEnd = computed(() =>
	focusLine.value
		? lyricPresentationEnd(focusLine.value, nextLine.value?.start_seconds ?? null)
		: null,
);
const presentationDuration = computed(() => {
	const line = focusLine.value;
	const end = presentationEnd.value;
	if (!line || line.start_seconds === null || end === null) return 0;
	return Math.max(0.25, end - line.start_seconds);
});
const lineProgress = computed(() => {
	const line = focusLine.value;
	if (activeIndex.value === null || !line || line.start_seconds === null) return 0;
	const start = line.start_seconds;
	return Math.min(
		100,
		Math.max(0, ((position.value - start) / presentationDuration.value) * 100),
	);
});
const modeCaption = computed(() => {
	if (lyrics.value?.state === "not_found") return "No match in LRCLIB";
	if (lyrics.value?.state === "instrumental") return "Instrumental";
	if (!lyrics.value?.synchronized) return "Plain lyrics";
	switch (mode.value) {
		case "cinematic":
			return "One line, center stage";
		case "edit":
			return `${editVariant.value[0]?.toUpperCase()}${editVariant.value.slice(1)} edit`;
		case "words":
			return "Estimated word timing";
		case "read":
			return "Read without auto-follow";
		default:
			return following.value ? "Following playback" : "Browsing lyrics";
	}
});
const editComponents = {
	orbit: LyricsEditOrbit,
	spiral: LyricsEditSpiral,
	impact: LyricsEditImpact,
};
const editComponent = computed(() => editComponents[editVariant.value]);
const editComponentProps = computed(() => {
	const sequence = editSequence.value;
	const shared = {
		tokens: sequence.tokens,
		activeWordIndex: sequence.activeWordIndex,
		lineIndex: focusIndex.value ?? 0,
	};
	if (editVariant.value === "orbit") {
		return {
			...shared,
			contextTokens: sequence.contextTokens,
			verseLines: sequence.verseLines,
		};
	}
	return { ...shared, progress: lineProgress.value };
});

watch(
	() => lyrics.value?.synchronized ?? null,
	(value) => setSynchronized(value),
	{ immediate: true },
);

watch(
	() => props.trackId,
	(trackId) => {
		following.value = true;
		selectTrack(trackId);
		result.set(null);
		void workflow.load(trackId);
	},
	{ immediate: true },
);
watch([activeIndex, () => props.visible], () => {
	if (!props.visible || mode.value !== "synced" || !following.value || activeIndex.value === null)
		return;
	void nextTick(scrollToCurrent);
});

function scrollToCurrent() {
	if (activeIndex.value === null) return;
	viewport.value
		?.querySelector<HTMLElement>(`[data-lyric-line="${activeIndex.value}"]`)
		?.scrollIntoView({
			block: "center",
			behavior: reducedMotion.value === "reduce" ? "auto" : "smooth",
		});
}
function stopFollowing() {
	if (mode.value === "synced") following.value = false;
}
function resumeFollowing() {
	following.value = true;
	scrollToCurrent();
}
function refresh() {
	following.value = true;
	void workflow.load(props.trackId, true);
}
onScopeDispose(workflow.dispose);
</script>

<template>
	<section class="lyrics-view" aria-label="Lyrics">
		<div class="lyrics-heading">
			<div class="lyrics-heading-copy">
				<p class="lyrics-title"><UIcon :name="icons.lyrics" /> Lyrics</p>
				<p v-if="lyrics" class="lyrics-caption">{{ modeCaption }}</p>
			</div>
			<div v-if="lyrics?.state === 'available'" class="lyrics-controls">
				<button
					v-if="lyrics.synchronized && mode === 'synced' && !following"
					type="button"
					class="lyrics-action"
					@click="resumeFollowing"
				>
					<UIcon :name="icons.arrowDown" /> Return to current line
				</button>
			</div>
		</div>

		<div
			ref="viewport"
			class="lyrics-viewport"
			:class="[`is-${mode}`]"
			@wheel.passive="stopFollowing"
			@touchstart.passive="stopFollowing"
		>
			<div v-if="result.loading.value && !lyrics" class="lyrics-lines" aria-busy="true">
				<div v-for="(width, index) in skeletonWidths" :key="index" class="lyric-line">
					<USkeleton class="h-7 rounded-md" :style="{ width }" />
				</div>
			</div>
			<div v-else-if="result.error.value" class="lyrics-state" role="alert">
				<UIcon :name="icons.caution" />
				<h3>Lyrics are taking the night off.</h3>
				<p>{{ result.error.value }}</p>
				<button type="button" class="lyrics-action" @click="refresh">
					<UIcon :name="icons.reload" /> Try again
				</button>
			</div>
			<div v-else-if="lyrics?.state === 'instrumental'" class="lyrics-state">
				<UIcon :name="icons.music" />
				<h3>No words needed.</h3>
				<p>LRCLIB marks this track as instrumental.</p>
			</div>
			<div v-else-if="lyrics?.state === 'not_found'" class="lyrics-state">
				<UIcon :name="icons.search" />
				<h3>No lyrics found.</h3>
				<p>This track has not reached LRCLIB yet.</p>
				<button type="button" class="lyrics-action" @click="refresh">
					<UIcon :name="icons.reload" /> Check again
				</button>
			</div>

			<div
				v-else-if="lyrics && (mode === 'synced' || mode === 'read')"
				class="lyrics-lines"
				:class="{ 'is-reading': mode === 'read' }"
			>
				<p
					v-for="(line, index) in lyrics.lines"
					:key="`${index}-${line.start_seconds ?? 'plain'}`"
					:data-lyric-line="index"
					class="lyric-line"
					:class="{
						'is-active': activeIndex === index,
						'is-past': mode === 'synced' && activeIndex !== null && index < activeIndex,
					}"
					:aria-current="activeIndex === index ? 'true' : undefined"
				>
					{{ line.text || "♪" }}
				</p>
			</div>

			<div
				v-else-if="lyrics && mode === 'cinematic'"
				class="cinematic-stage"
				aria-live="polite"
			>
				<Transition name="cinematic-shift" mode="out-in">
					<div v-if="focusLine" :key="focusIndex ?? 'active'" class="cinematic-frame">
						<p class="cinematic-context">{{ previousLine?.text || " " }}</p>
						<p class="cinematic-line">{{ focusLine.text }}</p>
						<p class="cinematic-context">{{ nextLine?.text || " " }}</p>
						<div class="cinematic-progress" aria-hidden="true">
							<span :style="{ transform: `scaleX(${lineProgress / 100})` }" />
						</div>
					</div>
					<div v-else key="silence" class="lyrics-silence" aria-hidden="true" />
				</Transition>
			</div>

			<div v-else-if="lyrics && mode === 'edit'" class="edit-stage" aria-live="polite">
				<Transition name="edit-cut" mode="out-in">
					<component
						v-if="focusLine"
						:is="editComponent"
						:key="editVariant"
						v-bind="editComponentProps"
					/>
					<div v-else key="silence" class="lyrics-silence" aria-hidden="true" />
				</Transition>
			</div>

			<div v-else-if="lyrics && mode === 'words'" class="words-stage" aria-live="polite">
				<template v-if="focusLine">
					<p class="words-line">
						<span
							v-for="(word, index) in focusWords"
							:key="`${focusIndex}-${index}-${word}`"
							:class="{
								'is-heard': activeWordIndex !== null && index <= activeWordIndex,
								'is-current': activeWordIndex === index,
							}"
							>{{ word }}</span
						>
					</p>
					<p class="words-next">{{ nextLine?.text }}</p>
				</template>
				<div v-else class="lyrics-silence" aria-hidden="true" />
			</div>
		</div>

		<footer v-if="lyrics" class="lyrics-footer">
			<p v-if="lyrics.stale">Showing the last saved copy while LRCLIB is unavailable.</p>
			<p v-else-if="lyrics.cached">Loaded from NaHörMaar's lyrics cache.</p>
			<a :href="lyrics.provider_url" target="_blank" rel="noopener noreferrer"
				>Lyrics from {{ lyrics.provider }} <UIcon :name="icons.external"
			/></a>
		</footer>
	</section>
</template>

<style scoped>
.lyrics-view {
	position: relative;
	z-index: 2;
	display: flex;
	flex: 1;
	min-height: 0;
	flex-direction: column;
	padding-top: 1.25rem;
}
.lyrics-heading,
.lyrics-footer {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 1rem;
}
.lyrics-heading {
	min-height: 3.25rem;
	padding-bottom: 0.75rem;
}
.lyrics-heading-copy {
	flex: 0 0 auto;
}
.lyrics-title {
	display: flex;
	align-items: center;
	gap: 0.5rem;
	font-size: 0.875rem;
	font-weight: 650;
	color: var(--player-foreground);
}
.lyrics-title > span,
.lyrics-action > span,
.lyrics-footer > a > span {
	width: 1rem;
	height: 1rem;
}
.lyrics-caption,
.lyrics-footer {
	font-size: 0.75rem;
	color: color-mix(in srgb, var(--player-foreground) 82%, transparent);
}
.lyrics-caption {
	margin-top: 0.125rem;
}
.lyrics-controls {
	display: flex;
	min-width: 0;
	align-items: center;
	justify-content: flex-end;
	gap: 0.5rem;
}
.lyrics-action:focus-visible,
.lyrics-footer a:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
.lyrics-viewport {
	position: relative;
	min-height: 0;
	flex: 1;
	overflow-y: auto;
	overscroll-behavior: contain;
	scrollbar-color: color-mix(in srgb, var(--player-foreground) 30%, transparent) transparent;
	scrollbar-width: thin;
	mask-image: linear-gradient(
		to bottom,
		transparent,
		#000 4rem,
		#000 calc(100% - 4rem),
		transparent
	);
}
.lyrics-lines {
	display: flex;
	min-height: 100%;
	flex-direction: column;
	justify-content: center;
	gap: 0.375rem;
	padding: 5rem 0;
}
.lyric-line {
	width: fit-content;
	max-width: min(32ch, 90%);
	padding: 0.25rem 0;
	font-size: clamp(1.25rem, 2.25vw, 2.25rem);
	font-weight: 560;
	line-height: 1.28;
	letter-spacing: -0.025em;
	color: color-mix(in srgb, var(--player-foreground) 52%, transparent);
	transition:
		color 240ms cubic-bezier(0.16, 1, 0.3, 1),
		opacity 240ms cubic-bezier(0.16, 1, 0.3, 1),
		transform 240ms cubic-bezier(0.16, 1, 0.3, 1);
}
.lyrics-lines:not(:has(.is-active)) .lyric-line {
	color: color-mix(in srgb, var(--player-foreground) 82%, transparent);
}
.lyric-line.is-active {
	color: var(--player-foreground);
	transform: translateX(0.5rem);
}
.lyric-line.is-past {
	opacity: 0.38;
}
.lyrics-lines.is-reading {
	justify-content: flex-start;
	gap: 0.875rem;
	padding-block: 3.5rem;
}
.lyrics-lines.is-reading .lyric-line {
	max-width: min(46ch, 92%);
	font-size: clamp(1rem, 1.45vw, 1.375rem);
	font-weight: 500;
	color: color-mix(in srgb, var(--player-foreground) 82%, transparent);
}
.lyrics-lines.is-reading .lyric-line.is-active {
	color: var(--player-foreground);
	transform: none;
}
.cinematic-stage,
.edit-stage,
.words-stage {
	display: grid;
	min-height: 100%;
	place-items: center;
	overflow: hidden;
}
.lyrics-silence {
	width: 100%;
	min-height: 12rem;
}
.cinematic-frame {
	display: grid;
	width: min(100%, 70rem);
	min-height: 20rem;
	grid-template-rows: 1fr auto 1fr auto;
	align-items: center;
	gap: 1.5rem;
	padding: 3rem 1rem;
	text-align: center;
}
.cinematic-line {
	max-width: 22ch;
	justify-self: center;
	font-size: clamp(2.5rem, 6vw, 5.75rem);
	font-weight: 720;
	line-height: 0.98;
	letter-spacing: -0.04em;
	text-wrap: balance;
	color: var(--player-foreground);
	text-shadow: 0 0.125rem 1.75rem color-mix(in srgb, #000 28%, transparent);
}
.cinematic-context {
	max-width: 34ch;
	justify-self: center;
	font-size: clamp(0.875rem, 1.35vw, 1.125rem);
	font-weight: 520;
	line-height: 1.35;
	color: color-mix(in srgb, var(--player-foreground) 42%, transparent);
}
.cinematic-progress {
	width: min(18rem, 42vw);
	height: 0.125rem;
	justify-self: center;
	overflow: hidden;
	background: color-mix(in srgb, var(--player-foreground) 18%, transparent);
}
.cinematic-progress span {
	display: block;
	width: 100%;
	height: 100%;
	transform-origin: left;
	background: var(--player-foreground);
	transition: transform 220ms linear;
}
.cinematic-shift-enter-active,
.cinematic-shift-leave-active {
	transition:
		opacity 320ms cubic-bezier(0.16, 1, 0.3, 1),
		filter 320ms cubic-bezier(0.16, 1, 0.3, 1),
		transform 320ms cubic-bezier(0.16, 1, 0.3, 1);
}
.cinematic-shift-enter-from {
	opacity: 0;
	filter: blur(0.75rem);
	transform: translateY(1.5rem) scale(0.97);
}
.cinematic-shift-leave-to {
	opacity: 0;
	filter: blur(0.5rem);
	transform: translateY(-1rem) scale(1.02);
}
.edit-stage {
	isolation: isolate;
	width: 100%;
	grid-template-columns: minmax(0, 1fr);
	align-items: stretch;
}
.edit-cut-enter-active,
.edit-cut-leave-active {
	transition:
		opacity 180ms ease-out,
		clip-path 260ms cubic-bezier(0.16, 1, 0.3, 1),
		transform 260ms cubic-bezier(0.16, 1, 0.3, 1);
}
.edit-cut-enter-from {
	opacity: 0;
	clip-path: inset(45% 0 45% 0);
	transform: scale(1.1) rotate(0deg);
}
.edit-cut-leave-to {
	opacity: 0;
	clip-path: inset(0 48% 0 48%);
	transform: scale(0.94) rotate(0deg);
}
.words-stage {
	align-content: center;
	gap: 2rem;
	padding: 4rem 1rem;
	text-align: center;
}
.words-line {
	display: flex;
	max-width: 26ch;
	flex-wrap: wrap;
	justify-content: center;
	gap: 0.08em 0.24em;
	font-size: clamp(2.25rem, 5.5vw, 5rem);
	font-weight: 700;
	line-height: 1.02;
	letter-spacing: -0.04em;
}
.words-line span {
	color: color-mix(in srgb, var(--player-foreground) 28%, transparent);
	transition:
		color 180ms cubic-bezier(0.16, 1, 0.3, 1),
		filter 180ms cubic-bezier(0.16, 1, 0.3, 1),
		transform 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.words-line span.is-heard {
	color: color-mix(in srgb, var(--player-foreground) 72%, transparent);
}
.words-line span.is-current {
	filter: drop-shadow(0 0.35rem 1rem color-mix(in srgb, #000 30%, transparent));
	color: var(--player-foreground);
	transform: translateY(-0.12em) scale(1.06);
}
.words-next {
	max-width: 36ch;
	font-size: 0.875rem;
	color: color-mix(in srgb, var(--player-foreground) 38%, transparent);
}
.lyrics-state {
	display: flex;
	min-height: 100%;
	max-width: 30rem;
	flex-direction: column;
	justify-content: center;
	align-items: flex-start;
	gap: 0.75rem;
	padding: 3rem 0;
}
.lyrics-state > span {
	width: 2rem;
	height: 2rem;
	color: color-mix(in srgb, var(--player-foreground) 62%, transparent);
}
.lyrics-state h3 {
	font-size: 1.5rem;
	font-weight: 600;
	letter-spacing: -0.025em;
}
.lyrics-state p {
	color: color-mix(in srgb, var(--player-foreground) 62%, transparent);
}
.lyrics-action {
	display: inline-flex;
	min-height: 2.25rem;
	align-items: center;
	gap: 0.5rem;
	border-radius: 0.5rem;
	background: var(--player-control-bg);
	padding: 0.375rem 0.75rem;
	font-size: 0.75rem;
	color: var(--player-foreground);
	transition: background-color 160ms cubic-bezier(0.16, 1, 0.3, 1);
}
.lyrics-action:hover {
	background: var(--player-control-hover);
}
.lyrics-footer {
	min-height: 2.5rem;
	padding-top: 0.75rem;
}
.lyrics-footer a {
	display: inline-flex;
	align-items: center;
	gap: 0.375rem;
	margin-left: auto;
	text-underline-offset: 0.2em;
}
.lyrics-footer a:hover {
	color: var(--player-foreground);
	text-decoration: underline;
}
@container workspace (max-width: 760px) {
	.lyrics-heading {
		align-items: flex-start;
		flex-direction: column;
	}
	.lyrics-controls {
		width: 100%;
	}
	.lyrics-controls {
		align-items: flex-start;
		justify-content: flex-start;
	}
}
@container workspace (max-width: 600px) {
	.lyrics-view {
		padding-top: 0.75rem;
	}
	.lyrics-viewport {
		mask-image: linear-gradient(
			to bottom,
			transparent,
			#000 2rem,
			#000 calc(100% - 2rem),
			transparent
		);
	}
	.lyrics-lines {
		padding-block: 3rem;
	}
	.lyric-line {
		font-size: 1.25rem;
	}
	.cinematic-frame,
	.words-stage {
		padding-inline: 0.25rem;
	}
	.cinematic-line {
		font-size: clamp(2rem, 10vw, 3.5rem);
	}
	.words-line {
		font-size: clamp(2rem, 11vw, 3.5rem);
	}
	.lyrics-footer > p {
		display: none;
	}
}
@media (prefers-reduced-motion: reduce) {
	.lyric-line,
	.lyrics-action,
	.cinematic-progress span,
	.words-line span,
	.cinematic-shift-enter-active,
	.cinematic-shift-leave-active,
	.edit-cut-enter-active,
	.edit-cut-leave-active {
		transition: none;
	}
}
</style>
