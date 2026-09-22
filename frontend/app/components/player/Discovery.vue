<script setup lang="ts">
const { icons } = useTheme();
const {
	player,
	radio,
	library,
	results,
	query,
	searchSource,
	searching,
	loadingMore,
	nextOffset,
	searchError,
	preview,
	previewUrl,
	previewError,
	previewPending,
	searchUpdate,
	previewUpdate,
	refreshing,
	refreshError,
	searchExpired,
	source,
	inputError,
	panelOpen,
	view,
	selected,
	importing,
	skipDuplicates,
	choices,
	selection,
	counts,
	submitting,
	loadingPlaylist,
	canReturnToPlaylist,
	backToPlaylist,
	startRadio,
	openPlaylist,
	submit,
	addResult,
	toggle,
	selectAll,
	importSelection,
	applyUpdate,
} = useDiscovery();
const launcher = ref<HTMLElement>();
const scrollArea = ref<HTMLElement>();
let scrollTop = 0;
watch([view, query, previewUrl, () => radio.version], () => {
	scrollTop = 0;
	if (scrollArea.value) scrollArea.value.scrollTop = 0;
});
watch(panelOpen, (open) => {
	if (!open) scrollTop = scrollArea.value?.scrollTop ?? scrollTop;
});
function restoreFocus() {
	if (!panelOpen.value && view.value !== "radio")
		launcher.value?.querySelector("input")?.focus({ preventScroll: true });
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
				v-model:provider="searchSource"
				:connected="player.connection === 'live'"
				:enabled="player.enabled"
				:error="inputError"
				:loading="submitting"
				@submit="submit"
				@playlist="openPlaylist"
			/>
		</div>
		<UButton
			v-if="query || previewUrl || radio.source"
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
					: view === 'playlist'
						? preview?.playlist?.title || 'Playlist'
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
					v-if="view === 'playlist'"
					:href="previewUrl"
					target="_blank"
					rel="noopener noreferrer"
					class="hover:underline"
					>{{ preview?.playlist?.title || "YouTube playlist" }}</a
				>
				<div v-else-if="view === 'radio'" class="flex min-w-0 items-center gap-2">
					<UButton
						v-if="canReturnToPlaylist"
						:icon="icons.arrowLeft"
						label="Back to playlist"
						color="neutral"
						variant="ghost"
						class="-ml-2 min-h-11 px-2"
						@click="backToPlaylist"
					/>
					<span class="truncate">Radio</span>
				</div>
				<span v-else>Find music</span>
			</template>
			<template #body>
				<div
					v-if="view === 'radio'"
					class="panel-results px-4 sm:px-6"
					:aria-busy="radio.loading"
				>
					<p class="text-lg font-medium text-highlighted mb-2">
						{{ radio.source?.title }}
					</p>
					<p class="text-sm text-muted mb-5">
						Similar tracks from YouTube Music. Your queue and current track stay in
						place.
					</p>
					<div v-if="radio.error" class="discovery-message" role="alert">
						<span>{{ radio.error }}</span>
						<UButton
							label="Try again"
							:icon="icons.reload"
							color="neutral"
							variant="ghost"
							class="min-h-11"
							:disabled="!player.enabled"
							@click="radio.source && radio.open(radio.source)"
						/>
					</div>
					<p v-else class="py-6 text-sm text-muted" role="status">
						{{
							radio.loading
								? "Preparing radio…"
								: "Start radio to automatically find and queue similar tracks as you listen."
						}}
					</p>
				</div>
				<template v-else>
					<div class="panel-search">
						<PlayerDiscoveryInput
							v-model="source"
							v-model:provider="searchSource"
							:connected="player.connection === 'live'"
							:enabled="player.enabled"
							:error="inputError"
							:loading="submitting"
							@submit="submit"
							@playlist="openPlaylist"
						/>
					</div>
					<div class="panel-update" aria-live="polite">
						<UButton
							v-if="view === 'search' ? searchUpdate : previewUpdate"
							label="Show updated results"
							:icon="icons.reload"
							color="neutral"
							variant="ghost"
							:disabled="importing || player.pending || previewPending || loadingMore"
							class="min-h-11"
							@click="applyUpdate"
						/>
						<p v-else-if="refreshError" class="text-sm text-muted">
							{{ refreshError }}
						</p>
						<p
							v-else-if="refreshing && (results.length || !!preview)"
							class="text-xs text-muted"
						>
							Checking for updates…
						</p>
					</div>
					<div ref="scrollArea" class="panel-results">
						<div v-if="view === 'search'" :aria-busy="searching || loadingMore">
							<p v-if="query" class="mb-3 text-sm text-muted">
								Results for “{{ query }}”
							</p>
							<p v-if="searching" role="status" class="sr-only">
								Searching
								{{
									searchSource === "youtube_music" ? "YouTube Music" : "YouTube"
								}}…
							</p>
							<PlayerCatalogList
								v-if="searching || results.length"
								:entries="results"
								:loading="searching && !results.length"
								:loading-more="loadingMore"
								:enabled="player.enabled"
								@add="addResult"
							/>
							<p v-else-if="!searchError" class="py-6 text-sm text-muted">
								{{
									query
										? "No tracks found. Try another title or artist."
										: "Search for a title or artist above."
								}}
							</p>
							<div v-if="searchError" role="alert" class="discovery-message">
								<span>{{ searchError }}</span>
								<UButton
									label="Try again"
									color="neutral"
									variant="ghost"
									:disabled="player.connection !== 'live'"
									@click="
										results.length && !searchExpired
											? library.loadMore()
											: library.search(query)
									"
								/>
							</div>
							<div v-if="results.length" class="discovery-footer">
								<p role="status" class="text-xs text-muted tabular-nums">
									{{ results.length }}
									{{ results.length === 1 ? "result" : "results" }}
								</p>
								<UButton
									v-if="nextOffset !== null && !searchError"
									label="Load more"
									:icon="icons.arrowDown"
									:loading="loadingMore"
									:disabled="loadingMore || player.connection !== 'live'"
									color="neutral"
									variant="ghost"
									class="min-h-11"
									@click="library.loadMore()"
								/>
							</div>
						</div>
						<div v-else :aria-busy="loadingPlaylist">
							<div class="playlist-heading">
								<div class="min-w-0">
									<p role="status" class="mt-1 text-xs text-muted">
										{{
											loadingPlaylist
												? `${preview?.entries.length ?? 0} tracks loaded`
												: `${preview?.entries.length ?? 0} tracks`
										}}
									</p>
								</div>
								<UButton
									v-if="!!preview"
									label="Radio from playlist"
									:icon="icons.radio"
									variant="ghost"
									color="neutral"
									class="min-h-11"
									:disabled="!player.enabled"
									@click="
										radio.open({
											kind: 'playlist',
											reference: preview.playlist?.reference,
											source_url: previewUrl,
											title: preview.playlist?.title || 'YouTube playlist',
										})
									"
								/>
								<UButton
									v-if="loadingPlaylist"
									label="Cancel"
									color="neutral"
									variant="ghost"
									@click="library.closePreview()"
								/>
								<UButton
									v-else-if="previewError"
									label="Reload playlist"
									color="neutral"
									variant="ghost"
									@click="openPlaylist(previewUrl)"
								/>
							</div>
							<p
								v-if="previewError || preview?.error"
								role="alert"
								class="my-3 text-sm text-error"
							>
								{{ previewError || preview?.error }}
							</p>
							<p v-if="preview?.source_has_more" class="my-3 text-sm text-muted">
								Showing the first {{ preview.entries.length }} entries. The rest of
								this playlist will not be imported.
							</p>

							<PlayerCatalogList
								v-if="preview?.entries.length || loadingPlaylist"
								:entries="preview?.entries ?? []"
								:loading="loadingPlaylist && !preview?.entries.length"
								selectable
								:selected="selected"
								:enabled="
									!!preview && !importing && !player.pending && !previewPending
								"
								@toggle="toggle"
							/>
							<p v-else-if="!!preview" class="py-6 text-sm text-muted">
								This playlist has no tracks available.
							</p>
						</div>
					</div>
				</template>
			</template>
			<template v-if="view === 'radio' || (view === 'playlist' && !!preview)" #footer>
				<div
					v-if="view === 'radio'"
					class="flex w-full flex-wrap items-center justify-between gap-3"
				>
					<p class="text-xs text-muted">
						{{
							player.snapshot?.radio?.state && player.snapshot.radio.state !== "off"
								? "Replaces the active radio. Queued tracks stay."
								: "Keeps three tracks ready. Your requests play first."
						}}
					</p>
					<UButton
						:label="
							player.snapshot?.radio?.state && player.snapshot.radio.state !== 'off'
								? 'Replace radio'
								: 'Start radio'
						"
						:icon="icons.radio"
						color="primary"
						class="min-h-11 ml-auto"
						:disabled="!player.enabled || radio.loading || !radio.preview"
						:loading="player.isPending('radio.started')"
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
						<span
							v-if="skipDuplicates && counts.skipped"
							class="text-xs text-muted"
							role="status"
							>{{ counts.skipped }} already playing, queued or selected twice</span
						>
					</div>
					<div v-if="!!preview && choices.length" class="playlist-selection">
						<UButton
							:label="
								selection.length === choices.length ? 'Deselect all' : 'Select all'
							"
							color="neutral"
							variant="ghost"
							class="min-h-11"
							:disabled="importing || previewPending"
							@click="selectAll"
						/>
						<span class="text-xs text-muted tabular-nums"
							>{{ selection.length }} selected</span
						>
						<UButton
							:label="`Add ${counts.added} ${counts.added === 1 ? 'track' : 'tracks'}`"
							:icon="icons.plus"
							color="primary"
							variant="solid"
							class="ml-auto min-h-11"
							:disabled="
								!player.enabled ||
								!counts.added ||
								!!previewError ||
								previewPending ||
								importing
							"
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
	.panel-search {
		padding-inline: 1.5rem;
	}
	.panel-results {
		padding-inline: 1.5rem;
	}
}
</style>
