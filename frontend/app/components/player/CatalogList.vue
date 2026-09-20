<script setup lang="ts">
import { catalogEntry, queuePresence, type CatalogTrack } from "#shared/catalog";
import { formatTime, trackTitle } from "#shared/player";
defineProps<{
	entries: CatalogTrack[];
	enabled: boolean;
	selectable?: boolean;
	selected?: Set<number>;
	loading?: boolean;
	loadingMore?: boolean;
}>();
const emit = defineEmits<{ add: [entry: CatalogTrack]; toggle: [index: number] }>();
const { icons } = useTheme();
const player = usePlayerStore();
</script>

<template>
	<ol
		class="catalog-list"
		:class="{ 'search-results': !selectable }"
		:aria-label="selectable ? 'Playlist tracks' : 'Search results'"
	>
		<li
			v-for="item in entries"
			:key="item.index"
			class="catalog-row"
			:class="{ 'is-unavailable': item.unavailable }"
		>
			<label v-if="selectable" class="catalog-select">
				<input
					type="checkbox"
					:checked="selected?.has(item.index)"
					:disabled="!enabled || !!item.unavailable || !item.source_url"
					@change="emit('toggle', item.index)"
				/>
				<span class="sr-only">Select {{ trackTitle(catalogEntry(item)) }}</span>
			</label>
			<PlayerTrackArtwork :entry="catalogEntry(item)" class="catalog-cover" />
			<div class="catalog-copy">
				<a
					v-if="item.source_url"
					:href="item.source_url"
					target="_blank"
					rel="noopener noreferrer"
					class="catalog-title"
					:title="trackTitle(catalogEntry(item))"
					>{{ trackTitle(catalogEntry(item)) }}</a
				>
				<span v-else class="catalog-title">{{ item.title || "Unavailable video" }}</span>
				<p class="catalog-detail">
					<PlayerArtistLink :entry="catalogEntry(item)" /><span>{{
						formatTime(item.duration_seconds)
					}}</span>
				</p>
				<p v-if="item.unavailable" class="mt-1 text-xs text-muted">
					{{ item.unavailable }}
				</p>
				<p
					v-else-if="queuePresence(item.source_url, player.snapshot)"
					class="mt-1 text-xs text-muted"
				>
					{{ queuePresence(item.source_url, player.snapshot) }}
				</p>
			</div>
			<UButton
				v-if="!selectable"
				:icon="icons.plus"
				color="neutral"
				variant="ghost"
				class="size-11 shrink-0 justify-center"
				:aria-label="`Add ${trackTitle(catalogEntry(item))} to queue`"
				:title="`Add ${trackTitle(catalogEntry(item))} to queue`"
				:disabled="!enabled || !!item.unavailable || !item.source_url"
				:loading="!!item.source_url && player.isAdding(item.source_url)"
				:aria-busy="!!item.source_url && player.isAdding(item.source_url)"
				@click="emit('add', item)"
			/>
		</li>
		<li
			v-for="index in loading ? 10 : loadingMore ? 4 : 0"
			:key="`skeleton-${index}`"
			class="catalog-row catalog-skeleton"
			aria-hidden="true"
		>
			<div v-if="selectable" class="catalog-select"><USkeleton class="size-4" /></div>
			<USkeleton class="catalog-cover rounded-md" />
			<div class="catalog-copy">
				<USkeleton class="h-4 w-3/4" />
				<div class="catalog-detail">
					<USkeleton class="h-3 w-1/3" />
					<USkeleton class="h-3 w-8" />
				</div>
			</div>
			<div v-if="!selectable" class="grid size-11 shrink-0 place-items-center">
				<USkeleton class="size-4" />
			</div>
		</li>
	</ol>
</template>

<style scoped>
.catalog-list {
	display: grid;
	gap: 0.25rem;
}
.catalog-row {
	display: flex;
	min-width: 0;
	align-items: center;
	gap: 1rem;
	padding: 0.875rem 0.5rem;
	border-radius: 0.5rem;
	transition: background-color 140ms ease-out;
}
.catalog-row:hover,
.catalog-row:focus-within {
	background: var(--ui-bg-muted);
}
.catalog-copy {
	min-width: 0;
	flex: 1;
}
.catalog-cover {
	width: 3rem;
	height: 3rem;
	flex-shrink: 0;
}
.catalog-title {
	display: block;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
	color: var(--ui-text-highlighted);
	font-weight: 500;
	font-size: 0.875rem;
}
a.catalog-title:hover {
	text-decoration: underline;
	text-underline-offset: 3px;
}
.catalog-detail {
	display: flex;
	flex-wrap: wrap;
	align-items: baseline;
	gap: 0.375rem 1rem;
	margin-top: 0.25rem;
	font-size: 0.75rem;
	color: var(--ui-text-muted);
}
.catalog-detail :first-child {
	min-width: 0;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}
.catalog-detail span {
	font-variant-numeric: tabular-nums;
}
.catalog-select {
	display: grid;
	place-items: center;
	flex-shrink: 0;
	width: 2.75rem;
	min-height: 2.75rem;
	cursor: pointer;
}
.catalog-select input {
	width: 1rem;
	height: 1rem;
	accent-color: var(--ui-primary);
	cursor: inherit;
}
.catalog-select:has(input:disabled) {
	cursor: default;
}
.is-unavailable .catalog-title {
	color: var(--ui-text-muted);
}
.is-unavailable .catalog-cover {
	filter: grayscale(1);
}
@media (prefers-reduced-motion: reduce) {
	.catalog-skeleton :deep(.animate-pulse) {
		animation: none;
	}
}
@container discovery-results (max-width: 500px) {
	.catalog-row {
		gap: 0.625rem;
		padding-inline: 0;
	}
	.catalog-title {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		white-space: normal;
		overflow-wrap: anywhere;
	}
	.catalog-cover {
		width: 2.75rem;
		height: 2.75rem;
	}
}
</style>
