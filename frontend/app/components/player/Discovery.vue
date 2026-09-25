<script setup lang="ts">
const { icons } = useTheme();
const {
	player,
	source,
	provider,
	inputError,
	panelOpen,
	view,
	query,
	playlistUrl,
	selected,
	skipDuplicates,
	importing,
	submitting,
	loadingMore,
	pendingTrackIds,
	radioTitle,
	page,
	entries,
	availableEntries,
	selectedEntries,
	duplicateCount,
	addCount,
	canSearch,
	canControl,
	loading,
	error,
	openPlaylist,
	refresh,
	more,
	submit,
	addResult,
	toggle,
	selectAll,
	importSelection,
	openTrackRadio,
	openPlaylistRadio,
	startRadio,
	backFromRadio,
} = useBackendDiscovery();

const launcher = ref<HTMLElement>();
const scrollArea = ref<HTMLElement>();
let scrollTop = 0;

watch([view, query, playlistUrl], () => {
	scrollTop = 0;
	if (scrollArea.value) scrollArea.value.scrollTop = 0;
});
watch(panelOpen, (open) => {
	if (!open) scrollTop = scrollArea.value?.scrollTop ?? scrollTop;
});

function restoreFocus() {
	if (!panelOpen.value) launcher.value?.querySelector("input")?.focus({ preventScroll: true });
}
function restoreScroll() {
	if (scrollArea.value) scrollArea.value.scrollTop = scrollTop;
}
</script>

<template>
	<section aria-label="Find music" class="discovery">
		<div ref="launcher">
			<PlayerDiscoveryInput
				v-model="source"
				v-model:provider="provider"
				:available="canSearch"
				:can-queue="canControl"
				:error="inputError"
				:loading="submitting"
				@submit="submit"
				@playlist="openPlaylist"
			/>
		</div>

		<UButton
			v-if="page || view === 'radio'"
			label="Back to results"
			color="neutral"
			variant="link"
			class="mt-2 min-h-11 px-0"
			@click="panelOpen = true"
		/>

		<USlideover
			v-model:open="panelOpen"
			:title="
				view === 'radio'
					? 'Radio'
					: page?.kind === 'playlist'
						? page.playlist_title || 'Playlist'
						: 'Find music'
			"
			:unmount-on-hide="false"
			:close="{ class: 'size-11 justify-center' }"
			:ui="{
				content: 'w-full max-w-none sm:max-w-2xl ring-0 discovery-panel',
				header: 'h-(--ui-header-height) shrink-0 border-0 px-4 sm:px-6',
				body: 'p-0 sm:p-0 flex min-h-0 flex-col',
				footer: 'border-0 p-4 sm:p-6',
			}"
			@after:enter="restoreScroll"
			@after:leave="restoreFocus"
		>
			<template #title>
				<a
					v-if="view !== 'radio' && page?.kind === 'playlist' && page.source_url"
					:href="page.source_url"
					target="_blank"
					rel="noopener noreferrer"
					class="hover:underline"
				>
					{{ page.playlist_title || "YouTube playlist" }}
				</a>
				<div v-else-if="view === 'radio'" class="flex min-w-0 items-center gap-2">
					<UButton
						:icon="icons.arrowLeft"
						:label="page?.kind === 'playlist' ? 'Back to playlist' : 'Back to results'"
						color="neutral"
						variant="ghost"
						class="-ml-2 min-h-11 px-2"
						@click="backFromRadio"
					/>
					<span class="truncate">Radio</span>
				</div>
				<span v-else>Find music</span>
			</template>

			<template #body>
				<div v-if="view === 'radio'" class="panel-results px-4 sm:px-6">
					<p class="mb-2 text-lg font-medium text-highlighted">{{ radioTitle }}</p>
					<p class="mb-5 text-sm text-muted">
						NaHoerMaar will keep finding similar tracks while this radio is active.
						Manual requests still play first.
					</p>
					<p v-if="player.error" class="discovery-message text-error" role="alert">
						{{ player.error }}
					</p>
					<p v-else class="py-6 text-sm text-muted">
						The current track and queued requests stay in place when the radio starts.
					</p>
				</div>

				<template v-else>
					<div class="panel-search">
						<PlayerDiscoveryInput
							v-model="source"
							v-model:provider="provider"
							:available="canSearch"
							:can-queue="canControl"
							:error="inputError"
							:loading="submitting"
							@submit="submit"
							@playlist="openPlaylist"
						/>
					</div>

					<div class="panel-update" aria-live="polite">
						<UButton
							v-if="page?.stale"
							label="Refresh results"
							:icon="icons.reload"
							color="neutral"
							variant="ghost"
							:loading="loading"
							@click="refresh"
						/>
						<p v-else-if="page?.refreshing" class="text-xs text-muted">
							Checking the provider for updates...
						</p>
					</div>

					<div ref="scrollArea" class="panel-results">
						<div v-if="!page || page.kind === 'search'">
							<p v-if="query" class="mb-3 text-sm text-muted">
								Results for "{{ query }}"
							</p>
							<p v-if="loading" role="status" class="sr-only">Searching...</p>
							<PlayerCatalogList
								v-if="loading || entries.length"
								:entries="entries"
								:state="player.state"
								:can-control="canControl"
								:pending-track-ids="pendingTrackIds"
								:loading="loading && !entries.length"
								:loading-more="loadingMore"
								@add="addResult"
								@radio="openTrackRadio"
							/>
							<p v-else-if="!error" class="py-6 text-sm text-muted">
								{{
									query
										? "No tracks found. Try another title or artist."
										: "Search for a title or artist above."
								}}
							</p>
							<div v-if="error" role="alert" class="discovery-message">
								<span>{{ error }}</span>
								<UButton
									v-if="query"
									label="Try again"
									:icon="icons.reload"
									color="neutral"
									variant="ghost"
									:disabled="!canSearch"
									@click="refresh"
								/>
							</div>
							<div v-if="entries.length" class="discovery-footer">
								<p role="status" class="text-xs text-muted tabular-nums">
									{{ entries.length }} of {{ page?.total ?? entries.length }}
								</p>
								<UButton
									v-if="page?.next_offset !== null"
									label="Load more"
									:icon="icons.arrowDown"
									:loading="loadingMore"
									:disabled="loadingMore || !canSearch"
									color="neutral"
									variant="ghost"
									class="min-h-11"
									@click="more"
								/>
							</div>
						</div>

						<div v-else :aria-busy="loading">
							<div class="playlist-heading">
								<p role="status" class="text-xs text-muted">
									{{ loading ? "Loading playlist..." : page.total + " tracks" }}
								</p>
								<UButton
									label="Radio from playlist"
									:icon="icons.radio"
									variant="ghost"
									color="neutral"
									class="min-h-11"
									:disabled="!canControl"
									@click="openPlaylistRadio"
								/>
							</div>
							<p v-if="page.source_has_more" class="my-3 text-sm text-muted">
								Showing the first {{ entries.length }} tracks from this playlist.
							</p>
							<div v-if="error" role="alert" class="discovery-message">
								<span>{{ error }}</span>
								<UButton
									label="Reload playlist"
									:icon="icons.reload"
									color="neutral"
									variant="ghost"
									@click="refresh"
								/>
							</div>
							<PlayerCatalogList
								v-if="entries.length || loading"
								:entries="entries"
								:state="player.state"
								:can-control="canControl && !importing"
								:selected="selected"
								:loading="loading && !entries.length"
								selectable
								@toggle="toggle"
							/>
							<p v-else class="py-6 text-sm text-muted">
								This playlist has no available tracks.
							</p>
						</div>
					</div>
				</template>
			</template>

			<template
				v-if="view === 'radio' || (page?.kind === 'playlist' && entries.length)"
				#footer
			>
				<div
					v-if="view === 'radio'"
					class="flex w-full flex-wrap items-center justify-between gap-3"
				>
					<p class="text-xs text-muted">
						{{
							player.state?.radio
								? "This replaces the active radio. Queued requests stay."
								: "Keeps similar tracks ready as the queue gets shorter."
						}}
					</p>
					<UButton
						:label="player.state?.radio ? 'Replace radio' : 'Start radio'"
						:icon="icons.radio"
						color="primary"
						class="ml-auto min-h-11"
						:disabled="!canControl"
						@click="startRadio"
					/>
				</div>
				<div v-else class="w-full space-y-3">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<UCheckbox
							v-model="skipDuplicates"
							label="Skip duplicates"
							:disabled="importing"
						/>
						<span v-if="skipDuplicates && duplicateCount" class="text-xs text-muted">
							{{ duplicateCount }} already playing or queued
						</span>
					</div>
					<div class="playlist-selection">
						<UButton
							:label="
								selectedEntries.length === availableEntries.length
									? 'Deselect all'
									: 'Select all'
							"
							color="neutral"
							variant="ghost"
							class="min-h-11"
							:disabled="importing"
							@click="selectAll"
						/>
						<span class="text-xs text-muted tabular-nums">
							{{ selectedEntries.length }} selected
						</span>
						<UButton
							:label="'Add ' + addCount + (addCount === 1 ? ' track' : ' tracks')"
							:icon="icons.plus"
							color="primary"
							variant="solid"
							class="ml-auto min-h-11"
							:disabled="!canControl || !addCount || importing"
							:loading="importing"
							:aria-busy="importing"
							@click="importSelection"
						/>
					</div>
				</div>
			</template>
		</USlideover>
	</section>
</template>

<style scoped>
.discovery {
	margin-bottom: 1.75rem;
}
.panel-search {
	padding: 0.5rem 1rem 1rem;
	flex-shrink: 0;
}
.panel-update {
	flex-shrink: 0;
}
.panel-update:not(:empty) {
	padding: 0 1rem 0.75rem;
}
.panel-results {
	flex: 1;
	min-height: 0;
	overflow-y: auto;
	overscroll-behavior: contain;
	scrollbar-gutter: stable;
	padding: 0 1rem 1rem;
	container-type: inline-size;
	container-name: discovery-results;
}
.discovery-footer,
.playlist-heading,
.playlist-selection,
.discovery-message {
	display: flex;
	align-items: center;
	gap: 0.75rem;
}
.discovery-footer,
.playlist-heading,
.discovery-message {
	justify-content: space-between;
	padding-block: 1rem;
}
.playlist-selection {
	width: 100%;
	flex-wrap: wrap;
}
.discovery-message {
	font-size: 0.875rem;
}
@media (prefers-reduced-motion: reduce) {
	:global(.discovery-panel) {
		animation: none !important;
		transition: none !important;
	}
}
@media (min-width: 640px) {
	.panel-search,
	.panel-results {
		padding-inline: 1.5rem;
	}
}
</style>
