<script setup lang="ts">
import type { LyricsEditVariant, LyricsMode } from "~/composables/useLyricsDisplay";

const props = defineProps<{ trackId: string }>();
const { icons } = useTheme();
const { mode, synchronized, editVariant, selectTrack, selectEditVariant, shuffleEditVariant } =
	useLyricsDisplay();
const open = ref(false);

const modes = computed(
	() =>
		[
			{ value: "synced", label: "Synced", icon: icons.value.list },
			{ value: "cinematic", label: "Cinematic", icon: icons.value.eye },
			{ value: "edit", label: "Edit", icon: icons.value.sparkles },
			{ value: "words", label: "Words", icon: icons.value.type },
			{ value: "read", label: "Read", icon: icons.value.lyrics },
		] satisfies Array<{ value: LyricsMode; label: string; icon: string }>,
);
const editVariants = computed(
	() =>
		[
			{ value: "orbit", label: "Orbit", detail: "Words circle the beat" },
			{ value: "spiral", label: "Spiral", detail: "Builds a winding word trail" },
			{ value: "impact", label: "Impact", detail: "Bold cuts and echoes" },
		] satisfies Array<{ value: LyricsEditVariant; label: string; detail: string }>,
);
const effectiveMode = computed<LyricsMode>(() =>
	synchronized.value === true ? mode.value : "read",
);
const selectedMode = computed(
	() => modes.value.find((option) => option.value === effectiveMode.value)!,
);

function selectMode(nextMode: LyricsMode) {
	if (synchronized.value !== true && nextMode !== "read") return;
	mode.value = nextMode;
}

function selectVariant(variant: LyricsEditVariant) {
	selectEditVariant(props.trackId, variant);
}

watch(
	() => props.trackId,
	(trackId) => selectTrack(trackId),
	{ immediate: true },
);
</script>

<template>
	<UPopover v-model:open="open" :ui="{ content: 'w-[23rem] max-w-[calc(100vw-2rem)]' }">
		<UButton
			color="neutral"
			variant="ghost"
			class="lyrics-display-trigger"
			aria-label="Lyrics display settings"
			title="Lyrics display"
		>
			<UIcon :name="selectedMode.icon" />
			<span>{{ selectedMode.label }}</span>
			<UIcon :name="icons.chevronDown" class="lyrics-display-chevron" />
		</UButton>
		<template #content>
			<div class="lyrics-display-menu">
				<div class="lyrics-display-heading">
					<p>Lyrics view</p>
					<span>Pick how the words move</span>
				</div>
				<div class="lyrics-mode-grid" role="radiogroup" aria-label="Lyrics display mode">
					<button
						v-for="option in modes"
						:key="option.value"
						type="button"
						role="radio"
						:aria-checked="effectiveMode === option.value"
						:disabled="synchronized !== true && option.value !== 'read'"
						:title="
							synchronized !== true && option.value !== 'read'
								? 'This view needs synchronized lyrics'
								: undefined
						"
						@click="selectMode(option.value)"
					>
						<UIcon :name="option.icon" />
						<span>{{ option.label }}</span>
					</button>
				</div>
				<p v-if="synchronized !== true" class="lyrics-timing-note" role="status">
					<UIcon :name="synchronized === null ? icons.clock : icons.info" />
					{{
						synchronized === null
							? "Checking whether this track has timed lyrics…"
							: "This track only has plain lyrics. Timed views will return on the next synced track."
					}}
				</p>
				<Transition name="edit-options">
					<div v-if="effectiveMode === 'edit'" class="lyrics-edit-options">
						<div class="lyrics-edit-heading">
							<span>Edit style</span>
							<button type="button" @click="shuffleEditVariant(trackId)">
								<UIcon :name="icons.reload" /> Shuffle
							</button>
						</div>
						<div role="radiogroup" aria-label="Edit style">
							<button
								v-for="variant in editVariants"
								:key="variant.value"
								type="button"
								role="radio"
								:aria-checked="editVariant === variant.value"
								@click="selectVariant(variant.value)"
							>
								<span
									><strong>{{ variant.label }}</strong
									>{{ variant.detail }}</span
								>
								<UIcon
									v-if="editVariant === variant.value"
									:name="icons.check"
									class="lyrics-edit-check"
								/>
							</button>
						</div>
					</div>
				</Transition>
			</div>
		</template>
	</UPopover>
</template>

<style scoped>
.lyrics-display-trigger {
	display: inline-flex;
	width: 7.25rem;
	flex: 0 0 7.25rem;
	min-height: 2.25rem;
	align-items: center;
	justify-content: flex-start;
	gap: 0.45rem;
	border-radius: 0.5rem;
	background: var(--player-control-bg);
	padding: 0.375rem 0.675rem;
	font-size: 0.75rem;
	font-weight: 600;
	color: var(--player-foreground);
	transition: background-color 160ms cubic-bezier(0.16, 1, 0.3, 1);
}
.lyrics-display-trigger:hover {
	background: var(--player-control-hover);
}
.lyrics-display-trigger > span:first-child,
.lyrics-display-chevron {
	flex: 0 0 1rem;
	width: 1rem;
	height: 1rem;
}
.lyrics-display-trigger > span:nth-child(2) {
	min-width: 0;
	flex: 1;
	text-align: left;
}
.lyrics-display-chevron {
	opacity: 0.55;
}
.lyrics-display-menu {
	padding: 0.5rem;
}
.lyrics-display-heading {
	padding: 0.625rem 0.625rem 0.75rem;
}
.lyrics-display-heading p {
	font-size: 0.875rem;
	font-weight: 700;
	color: var(--ui-text-highlighted);
}
.lyrics-display-heading span {
	display: block;
	margin-top: 0.125rem;
	font-size: 0.75rem;
	color: var(--ui-text-muted);
}
.lyrics-mode-grid {
	display: grid;
	grid-template-columns: repeat(5, minmax(0, 1fr));
	gap: 0.25rem;
}
.lyrics-mode-grid button {
	display: grid;
	min-width: 0;
	justify-items: center;
	gap: 0.375rem;
	border-radius: 0.5rem;
	padding: 0.625rem 0.25rem;
	font-size: 0.6875rem;
	color: var(--ui-text-muted);
}
.lyrics-mode-grid button:hover,
.lyrics-mode-grid button[aria-checked="true"] {
	background: var(--ui-bg-elevated);
	color: var(--ui-text-highlighted);
}
.lyrics-mode-grid button:disabled {
	opacity: 0.38;
	cursor: not-allowed;
}
.lyrics-mode-grid button[aria-checked="true"] {
	box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ui-primary) 44%, transparent);
}
.lyrics-mode-grid button > span:first-child {
	width: 1rem;
	height: 1rem;
}
.lyrics-timing-note {
	display: grid;
	grid-template-columns: 1rem minmax(0, 1fr);
	align-items: start;
	gap: 0.5rem;
	margin: 0.5rem 0.25rem 0;
	border-top: 1px solid var(--ui-border);
	padding: 0.75rem 0.375rem 0.25rem;
	font-size: 0.6875rem;
	line-height: 1.45;
	color: var(--ui-text-muted);
}
.lyrics-timing-note > span {
	width: 1rem;
	height: 1rem;
	margin-top: 0.0625rem;
	color: var(--ui-text-dimmed);
}
.lyrics-edit-options {
	margin-top: 0.5rem;
	border-top: 1px solid var(--ui-border);
	padding: 0.75rem 0.25rem 0.25rem;
}
.lyrics-edit-heading {
	display: flex;
	align-items: center;
	justify-content: space-between;
	padding: 0 0.375rem 0.5rem;
	font-size: 0.6875rem;
	font-weight: 700;
	letter-spacing: 0.055em;
	text-transform: uppercase;
	color: var(--ui-text-dimmed);
}
.lyrics-edit-heading button {
	display: inline-flex;
	align-items: center;
	gap: 0.3rem;
	border-radius: 0.375rem;
	padding: 0.25rem 0.375rem;
	letter-spacing: 0;
	text-transform: none;
}
.lyrics-edit-heading button:hover {
	background: var(--ui-bg-elevated);
	color: var(--ui-text-highlighted);
}
.lyrics-edit-heading button > span {
	width: 0.875rem;
	height: 0.875rem;
}
.lyrics-edit-options > div:last-child {
	display: grid;
	grid-template-columns: minmax(0, 1fr);
	gap: 0.25rem;
}
.lyrics-edit-options > div:last-child > button {
	display: grid;
	grid-template-columns: minmax(0, 1fr) auto;
	align-items: center;
	gap: 0.75rem;
	border-radius: 0.5rem;
	padding: 0.625rem;
	text-align: left;
	color: var(--ui-text-muted);
}
.lyrics-edit-options > div:last-child > button:hover,
.lyrics-edit-options > div:last-child > button[aria-checked="true"] {
	background: var(--ui-bg-elevated);
	color: var(--ui-text-highlighted);
}
.lyrics-edit-options strong,
.lyrics-edit-options > div:last-child > button span span {
	display: block;
}
.lyrics-edit-options strong {
	font-size: 0.8125rem;
	font-weight: 680;
}
.lyrics-edit-options > div:last-child > button span {
	min-width: 0;
	font-size: 0.6875rem;
	line-height: 1.35;
}
.lyrics-edit-check {
	width: 1rem;
	height: 1rem;
	color: var(--ui-primary);
}
.edit-options-enter-active,
.edit-options-leave-active {
	transition:
		opacity 180ms ease-out,
		transform 220ms cubic-bezier(0.16, 1, 0.3, 1);
}
.edit-options-enter-from,
.edit-options-leave-to {
	opacity: 0;
	transform: translateY(-0.5rem);
}
@media (max-width: 44rem) {
	.lyrics-display-trigger {
		width: 2.5rem;
		flex-basis: 2.5rem;
		justify-content: center;
		padding-inline: 0.5rem;
	}
	.lyrics-display-trigger > span:nth-child(2) {
		display: none;
	}
	.lyrics-display-chevron {
		display: none;
	}
}
@media (prefers-reduced-motion: reduce) {
	.lyrics-display-trigger,
	.edit-options-enter-active,
	.edit-options-leave-active {
		transition: none;
	}
}
</style>
