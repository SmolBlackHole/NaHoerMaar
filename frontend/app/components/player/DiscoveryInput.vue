<script setup lang="ts">
import { musicSource, type SearchProvider } from "../../core/models/catalog";

const source = defineModel<string>({ required: true });
const provider = defineModel<SearchProvider>("provider", { required: true });
defineProps<{
	available: boolean;
	canQueue: boolean;
	error: string;
	loading?: boolean;
}>();
const emit = defineEmits<{ submit: []; playlist: [url: string] }>();
const { icons } = useTheme();
const id = useId();
const input = useTemplateRef<HTMLInputElement>("input");

function clear() {
	source.value = "";
	input.value?.focus();
}

const parsed = computed(() => musicSource(source.value));
const playlistLink = computed(() => (parsed.value.kind === "video" ? parsed.value.playlist : null));
const submitLabel = computed(() =>
	parsed.value.kind === "video"
		? "Add track"
		: parsed.value.kind === "playlist"
			? "Open playlist"
			: "Search",
);
</script>

<template>
	<div class="discovery-input-wrap">
		<form class="discovery-form" @submit.prevent="emit('submit')">
			<div
				class="discovery-search"
				:class="{ 'is-invalid': error, 'is-disabled': !available }"
			>
				<UIcon :name="icons.search" class="search-icon size-5 shrink-0 text-muted" />
				<label class="sr-only" :for="id">Link, title or artist</label>
				<input
					:id="id"
					ref="input"
					v-model="source"
					type="text"
					placeholder="Link, title or artist"
					autocomplete="off"
					:maxlength="2048"
					class="discovery-input"
					:disabled="!available"
					:aria-invalid="!!error"
					:aria-describedby="error ? id + '-error' : undefined"
				/>
				<UTooltip v-if="source" text="Clear search">
					<UButton
						:icon="icons.close"
						aria-label="Clear search"
						type="button"
						color="neutral"
						variant="ghost"
						class="search-clear"
						:disabled="!available"
						@click="clear"
					/>
				</UTooltip>
				<USelect
					v-model="provider"
					:items="[
						{ label: 'YouTube Music', value: 'youtube_music' },
						{ label: 'Videos', value: 'youtube' },
					]"
					:disabled="!available"
					:trailing-icon="icons.chevronDown"
					:ui="{ content: 'min-w-44', item: 'min-h-11 items-center' }"
					aria-label="Search source"
					variant="none"
					class="search-source"
				>
					<template #default>
						<template v-if="provider === 'youtube_music'">
							<span class="source-full">YouTube Music</span>
							<span class="source-short">Music</span>
						</template>
						<span v-else>Videos</span>
					</template>
				</USelect>
			</div>
			<UTooltip :text="submitLabel">
				<UButton
					type="submit"
					:aria-label="submitLabel"
					:icon="
						parsed.kind === 'video'
							? icons.plus
							: parsed.kind === 'playlist'
								? icons.list
								: icons.search
					"
					size="lg"
					color="neutral"
					variant="solid"
					class="discovery-submit"
					:loading="loading"
					:aria-busy="loading"
					:disabled="
						loading ||
						!source.trim() ||
						!available ||
						(parsed.kind === 'video' && !canQueue)
					"
				>
					<span class="discovery-submit-label">{{ submitLabel }}</span>
				</UButton>
			</UTooltip>
		</form>
		<p v-if="error" :id="id + '-error'" role="alert" class="mt-2 text-sm text-error">
			{{ error }}
		</p>
		<UButton
			v-if="playlistLink"
			:icon="icons.list"
			label="Open playlist instead"
			color="neutral"
			variant="link"
			class="mt-2 px-0"
			:disabled="!available"
			@click="emit('playlist', playlistLink)"
		/>
	</div>
</template>

<style scoped>
.discovery-input-wrap {
	container-type: inline-size;
	container-name: discovery-input;
}
.discovery-search {
	display: flex;
	align-items: center;
	flex: 1;
	min-width: 0;
	min-height: 3rem;
	border: 1px solid var(--ui-border-accented);
	border-radius: 0.5rem;
	background: var(--ui-bg-elevated);
}
.discovery-search:has(.discovery-input:focus-visible) {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
.discovery-search.is-invalid {
	border-color: var(--ui-error);
}
.discovery-search.is-disabled {
	opacity: 0.5;
}
.search-icon {
	margin-left: 0.875rem;
}
.discovery-input {
	flex: 1;
	min-width: 0;
	width: 100%;
	min-height: 2.75rem;
	padding: 0.5rem 0.75rem;
	background: transparent;
	color: var(--ui-text-highlighted);
	font-size: 1rem;
	outline: none;
	caret-color: var(--ui-primary);
}
.discovery-input::placeholder {
	color: var(--ui-text-muted);
}
.search-source {
	flex-shrink: 0;
	margin-right: 0.25rem;
	min-height: 2.75rem;
	padding: 0.5rem 2rem 0.5rem 0.75rem;
	background: transparent;
	font-size: 0.8125rem;
	color: var(--ui-text);
	cursor: pointer;
}
.search-clear {
	flex-shrink: 0;
	justify-content: center;
	width: 2.75rem;
	min-height: 2.75rem;
	color: var(--ui-text-muted);
}
.search-source:hover {
	background: var(--ui-bg-accented);
}
.search-source:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: -3px;
}
.source-short {
	display: none;
}
.discovery-form {
	display: flex;
	gap: 0.5rem;
}
.discovery-submit {
	flex-shrink: 0;
	justify-content: center;
	min-width: 7.5rem;
	min-height: 3rem;
}
@container discovery-input (max-width: 600px) {
	.search-icon,
	.discovery-submit-label,
	.source-full {
		display: none;
	}
	.discovery-submit {
		min-width: 3rem;
		width: 3rem;
		padding-inline: 0;
	}
	.source-short {
		display: inline;
	}
}
</style>
