<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ContextMenuItem } from "@nuxt/ui";
import { formatDuration, type DiscoveryEntry } from "../../core/models/catalog";
import type { PlayerState } from "../../core/models/player";

const props = defineProps<{
	entries: DiscoveryEntry[];
	state: PlayerState | null;
	canControl: boolean;
	selectable?: boolean;
	selected?: Set<number>;
	pendingTrackIds?: Set<string>;
	loading?: boolean;
	loadingMore?: boolean;
}>();
const emit = defineEmits<{
	add: [entry: DiscoveryEntry];
	radio: [entry: DiscoveryEntry];
	toggle: [position: number];
}>();
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const reactions = core.stores.useLibraryStore();
const { icons } = useTheme();

watch(
	() => [props.entries.map(({ track }) => track.id).join(","), session.status] as const,
	([, status]) => {
		if (status === "authenticated")
			void reactions.load(props.entries.map(({ track }) => track.id));
	},
	{ immediate: true },
);

function unavailable(entry: DiscoveryEntry) {
	return entry.source.availability === "unavailable";
}
function presence(trackId: string): string | null {
	if (props.state?.runtime.current?.track.id === trackId) return "Now playing";
	return props.state?.queue.some(({ request }) => request.track.id === trackId)
		? "Already queued"
		: null;
}
function contextItems(entry: DiscoveryEntry): ContextMenuItem[][] {
	return [
		[
			{
				label: `Add ${entry.track.title} to queue`,
				icon: icons.value.plus,
				disabled: !props.canControl || unavailable(entry),
				onSelect: () => emit("add", entry),
			},
			{
				label: "Start a radio from this track",
				icon: icons.value.radio,
				disabled: !props.canControl || unavailable(entry),
				onSelect: () => emit("radio", entry),
			},
		],
	];
}
</script>

<template>
	<ol
		class="catalog-list"
		:class="{ 'search-results': !selectable }"
		:aria-label="selectable ? 'Playlist tracks' : 'Search results'"
	>
		<SharedTrackRow
			v-for="item in entries"
			:key="item.source.id + ':' + item.position"
			class="catalog-row"
			identity-class="catalog-identity"
			:class="{ 'is-unavailable': unavailable(item) }"
			:entry="item.track"
			:title="item.track.title"
			:artist-names="item.track.artists.map(({ name }) => name)"
			:source-url="item.source.source_url"
			:metadata="formatDuration(item.track.duration_seconds)"
			:context-items="selectable ? [] : contextItems(item)"
			multiline
		>
			<template v-if="selectable" #leading>
				<label class="catalog-select">
					<input
						type="checkbox"
						:checked="selected?.has(item.position)"
						:disabled="!canControl || unavailable(item)"
						@change="emit('toggle', item.position)"
					/>
					<span class="sr-only">Select {{ item.track.title }}</span>
				</label>
			</template>
			<template #note>
				<p v-if="unavailable(item)" class="mt-0.5 text-xs text-muted">Track unavailable</p>
				<p v-else-if="presence(item.track.id)" class="mt-0.5 text-xs text-muted">
					{{ presence(item.track.id) }}
				</p>
			</template>
			<template #actions>
				<template v-if="!selectable">
					<LibraryReactionActions
						:track-id="item.track.id"
						:title="item.track.title"
						compact
						:show-counts="false"
						:show-details="false"
					/>
					<LibraryPlaylistAction
						:track="{ track_id: item.track.id, preferred_source_id: item.source.id }"
						:title="item.track.title"
					/>
					<UTooltip :text="'Add ' + item.track.title + ' to queue'">
						<UButton
							:icon="icons.plus"
							color="neutral"
							variant="ghost"
							class="size-11 shrink-0 justify-center"
							:aria-label="'Add ' + item.track.title + ' to queue'"
							:disabled="!canControl || unavailable(item)"
							:loading="pendingTrackIds?.has(item.track.id)"
							:aria-busy="pendingTrackIds?.has(item.track.id)"
							@click="emit('add', item)"
						/>
					</UTooltip>
					<UTooltip text="Start a radio from this track">
						<UButton
							:icon="icons.radio"
							color="neutral"
							variant="ghost"
							class="size-11 shrink-0 justify-center"
							:aria-label="'Start a radio from ' + item.track.title"
							:disabled="!canControl || unavailable(item)"
							@click="emit('radio', item)"
						/>
					</UTooltip>
				</template>
			</template>
		</SharedTrackRow>
		<SharedTrackRow
			v-for="index in loading ? 10 : loadingMore ? 4 : 0"
			:key="'skeleton-' + index"
			class="catalog-row catalog-skeleton"
			identity-class="catalog-identity"
			loading
		>
			<template v-if="selectable" #leading>
				<div class="catalog-select"><USkeleton class="size-4" /></div>
			</template>
			<template v-if="!selectable" #actions>
				<div class="flex shrink-0 items-center gap-1">
					<USkeleton class="size-10 rounded-lg" />
					<USkeleton class="size-10 rounded-lg" />
					<USkeleton class="size-10 rounded-lg" />
				</div>
				<div class="grid size-11 shrink-0 place-items-center">
					<USkeleton class="size-4" />
				</div>
				<div class="grid size-11 shrink-0 place-items-center">
					<USkeleton class="h-1 w-4" />
				</div>
			</template>
		</SharedTrackRow>
	</ol>
</template>

<style scoped>
.catalog-list {
	display: grid;
	gap: 0.25rem;
}
:deep(.catalog-row) {
	display: flex;
	align-items: center;
	gap: 1rem;
}
:deep(.catalog-identity) {
	min-width: 0;
	flex: 1;
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
:deep(.is-unavailable .catalog-identity) {
	color: var(--ui-text-muted);
}
:deep(.is-unavailable .catalog-identity .track-identity__artwork) {
	filter: grayscale(1);
}
@media (prefers-reduced-motion: reduce) {
	:deep(.catalog-skeleton .animate-pulse) {
		animation: none;
	}
}
@container discovery-results (max-width: 500px) {
	:deep(.catalog-row) {
		gap: 0.625rem;
	}
	:deep(.catalog-identity) {
		--track-artwork-size: 2.75rem;
	}
}
</style>
