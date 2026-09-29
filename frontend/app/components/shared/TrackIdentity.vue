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
	},
);

const artist = computed(() => props.artistNames.join(", ") || "Unknown artist");
</script>

<template>
	<div
		v-bind="$attrs"
		class="track-identity"
		:class="[`track-identity--${size}`, { 'track-identity--multiline': multiline }]"
	>
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
	.track-identity :deep(.animate-pulse) {
		animation: none;
	}
}
</style>
