<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ContextMenuItem } from "@nuxt/ui";

interface ArtworkEntry {
	artwork_url?: string | null;
	thumbnail_url?: string | null;
}

defineOptions({ inheritAttrs: false });

const props = withDefaults(
	defineProps<{
		entry?: ArtworkEntry | null;
		title?: string | null;
		artistNames?: readonly string[];
		sourceUrl?: string | null;
		artistUrl?: string | null;
		metadata?: string | null;
		size?: "sm" | "md" | "lg";
		loading?: boolean;
		multiline?: boolean;
		position?: number | null;
		reorderable?: boolean;
		reorderDisabled?: boolean;
		identityClass?: string;
		contextItems?: ContextMenuItem[][];
	}>(),
	{
		entry: null,
		title: null,
		artistNames: () => [],
		sourceUrl: null,
		artistUrl: null,
		metadata: null,
		size: "lg",
		loading: false,
		multiline: false,
		position: null,
		reorderable: false,
		reorderDisabled: false,
		identityClass: "",
		contextItems: () => [],
	},
);
const emit = defineEmits<{ move: [direction: -1 | 1] }>();
</script>

<template>
	<UContextMenu :items="contextItems">
		<li v-bind="$attrs" class="track-row" :aria-hidden="loading ? 'true' : undefined">
			<SharedTrackIdentity
				:entry="entry"
				:title="title"
				:artist-names="artistNames"
				:source-url="sourceUrl"
				:artist-url="artistUrl"
				:metadata="metadata"
				:size="size"
				:loading="loading"
				:multiline="multiline"
				:position="position"
				:reorderable="reorderable"
				:reorder-disabled="reorderDisabled"
				class="track-row__identity"
				:class="identityClass"
				@move="emit('move', $event)"
			>
				<template v-if="$slots.leading" #leading><slot name="leading" /></template>
				<template v-if="$slots.title" #title><slot name="title" /></template>
				<template v-if="$slots.artist" #artist><slot name="artist" /></template>
				<template v-if="$slots.metadata" #metadata><slot name="metadata" /></template>
				<template v-if="$slots.note" #note><slot name="note" /></template>
			</SharedTrackIdentity>
			<slot name="details" />
			<slot name="actions" />
			<slot name="menu" />
			<slot />
		</li>
	</UContextMenu>
</template>

<style scoped>
.track-row {
	min-width: 0;
	min-height: 4.75rem;
	padding: 0.75rem 0.5rem;
	border-radius: 0.5rem;
	transition: background-color 140ms ease-out;
}
.track-row:hover,
.track-row:focus-within {
	background: var(--ui-bg-muted);
}
@container workspace (max-width: 600px) {
	.track-row {
		padding-block: 1rem;
		padding-inline: 0;
	}
}
@media (prefers-reduced-motion: reduce) {
	.track-row {
		transition: none;
	}
}
</style>
