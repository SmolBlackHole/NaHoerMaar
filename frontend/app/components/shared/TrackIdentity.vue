<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
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
	},
);
const emit = defineEmits<{ move: [direction: -1 | 1] }>();
const { icons } = useTheme();

const artist = computed(() => props.artistNames.join(", ") || "Unknown artist");
const orderLabel = computed(() => (props.title ? `Reorder ${props.title}` : "Reorder track"));
</script>

<template>
	<div
		v-bind="$attrs"
		class="track-identity"
		:class="[`track-identity--${size}`, { 'track-identity--multiline': multiline }]"
	>
		<slot name="leading">
			<USkeleton
				v-if="loading && (reorderable || position !== null)"
				class="track-identity__order-skeleton size-4"
			/>
			<button
				v-else-if="reorderable"
				type="button"
				class="track-identity__order track-reorder-handle"
				:disabled="reorderDisabled"
				:aria-label="orderLabel"
				aria-description="Drag to reorder. Use the up and down arrow keys for precise moves."
				aria-keyshortcuts="ArrowUp ArrowDown"
				@keydown.up.prevent="emit('move', -1)"
				@keydown.down.prevent="emit('move', 1)"
			>
				<span v-if="position !== null" class="track-identity__position">{{
					String(position).padStart(2, "0")
				}}</span>
				<UIcon :name="icons.drag" class="track-identity__grip size-4" />
			</button>
			<span
				v-else-if="position !== null"
				class="track-identity__static-position text-xs tabular-nums text-muted"
				>{{ String(position).padStart(2, "0") }}</span
			>
		</slot>
		<USkeleton v-if="loading" class="track-identity__artwork rounded-lg" />
		<PlayerTrackArtwork v-else :entry="entry" class="track-identity__artwork" />
		<div class="track-identity__copy">
			<template v-if="loading">
				<USkeleton class="track-identity__title-skeleton h-4" />
				<USkeleton class="track-identity__artist-skeleton h-3" />
			</template>
			<template v-else>
				<slot name="title">
					<UTooltip v-if="sourceUrl" :text="`Open ${title ?? 'track'} in a new tab`">
						<a
							:href="sourceUrl"
							target="_blank"
							rel="noopener noreferrer"
							class="track-identity__title hover:underline"
						>
							{{ title ?? "Unknown track" }}
						</a>
					</UTooltip>
					<span v-else class="track-identity__title">{{ title ?? "Unknown track" }}</span>
				</slot>
				<div class="track-identity__details">
					<slot name="artist">
						<a
							v-if="artistUrl"
							:href="artistUrl"
							target="_blank"
							rel="noopener noreferrer"
							class="track-identity__artist hover:text-highlighted hover:underline"
						>
							{{ artist }}
						</a>
						<span v-else class="track-identity__artist">{{ artist }}</span>
					</slot>
					<span v-if="metadata" class="track-identity__metadata">{{ metadata }}</span>
					<slot name="metadata" />
				</div>
				<slot name="note" />
			</template>
		</div>
	</div>
</template>

<style scoped>
.track-identity {
	--track-artwork-size: 3rem;
	display: flex;
	min-width: 0;
	align-items: center;
	gap: 0.75rem;
}
.track-identity--sm {
	--track-artwork-size: 2.5rem;
	gap: 0.625rem;
}
.track-identity--md {
	--track-artwork-size: 2.75rem;
}
.track-identity__order,
.track-identity__static-position,
.track-identity__order-skeleton {
	position: relative;
	display: grid;
	width: 2.5rem;
	height: 2.5rem;
	flex: 0 0 2.5rem;
	place-items: center;
}
.track-identity__order {
	touch-action: none;
	cursor: grab;
	color: var(--ui-text-muted);
	border-radius: 0.5rem;
}
.track-identity__order:active:not(:disabled) {
	cursor: grabbing;
}
.track-identity__order:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
.track-identity__order:disabled {
	cursor: default;
	opacity: 0.45;
}
.track-identity__position,
.track-identity__grip {
	grid-area: 1 / 1;
	transition: opacity 140ms ease-out;
}
.track-identity__grip {
	opacity: 0;
}
.track-identity__order:focus-visible .track-identity__grip {
	opacity: 1;
}
.track-identity__order:focus-visible .track-identity__position {
	opacity: 0;
}
.track-identity__artwork {
	width: var(--track-artwork-size);
	height: var(--track-artwork-size);
	flex: 0 0 var(--track-artwork-size);
}
.track-identity__copy {
	min-width: 0;
	flex: 1;
}
.track-identity__title {
	display: block;
	max-width: 100%;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
	color: var(--ui-text-highlighted);
	font-size: 0.875rem;
	font-weight: 600;
	line-height: 1.25rem;
}
.track-identity__details {
	display: flex;
	min-width: 0;
	align-items: baseline;
	gap: 0.25rem 0.75rem;
	margin-top: 0.125rem;
	color: var(--ui-text-muted);
	font-size: 0.75rem;
	line-height: 1.125rem;
}
.track-identity__artist {
	min-width: 0;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}
.track-identity__metadata {
	flex-shrink: 0;
	font-variant-numeric: tabular-nums;
}
.track-identity__title-skeleton {
	width: min(100%, 18rem);
}
.track-identity__artist-skeleton {
	width: 8rem;
	margin-top: 0.375rem;
}
.track-identity--multiline .track-identity__title {
	display: -webkit-box;
	-webkit-box-orient: vertical;
	-webkit-line-clamp: 2;
	line-clamp: 2;
	white-space: normal;
	overflow-wrap: anywhere;
}
@media (prefers-reduced-motion: reduce) {
	.track-identity__position,
	.track-identity__grip {
		transition: none;
	}
	.track-identity :deep(.animate-pulse) {
		animation: none;
	}
}
@media (hover: hover) and (pointer: fine) {
	.track-identity:hover .track-identity__grip {
		opacity: 1;
	}
	.track-identity:hover .track-identity__position {
		opacity: 0;
	}
}
@media (hover: none) {
	.track-identity__grip {
		opacity: 1;
	}
	.track-identity__position {
		opacity: 0;
	}
}
</style>
