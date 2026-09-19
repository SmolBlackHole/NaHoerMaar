<script setup lang="ts">
import { formatTime, formatWait, queueWaits, trackTitle, youtubeVideoId } from "#shared/player";
import { usePlayerStore } from "~/stores/player";

const player = usePlayerStore();
const { icons } = useTheme();
const source = ref("");
const inputError = ref("");
const feedback = ref("");
const adding = ref(false);
const clearing = ref<number | null>(null);
const dragging = ref<{ id: string; revision: number } | null>(null);
const dropTarget = ref<string | null>(null);
const { now } = usePlaybackPosition();
const waits = computed(() =>
	player.connection === "live" ? queueWaits(player.snapshot, now.value) : [],
);
const queue = computed(() => player.snapshot?.upcoming ?? []);

async function add() {
	inputError.value = "";
	const submitted = source.value.trim();
	if (!youtubeVideoId(submitted)) {
		inputError.value = "Paste a link to a single YouTube video.";
		return;
	}
	adding.value = true;
	try {
		if (await player.add(submitted)) {
			if (source.value.trim() === submitted) source.value = "";
			feedback.value = "Track added to the queue.";
		}
	} finally {
		adding.value = false;
	}
}

async function move(index: number, direction: -1 | 1) {
	const entry = queue.value[index];
	if (!entry || !player.snapshot) return;
	const before = queue.value[index + (direction === -1 ? -1 : 2)]?.id ?? null;
	if (await player.move(entry.id, before, player.snapshot.queue_revision))
		feedback.value = "Queue order updated.";
}

function startDrag(event: DragEvent, id: string) {
	if (!player.enabled || !event.dataTransfer || !player.snapshot) {
		event.preventDefault();
		return;
	}
	dragging.value = { id, revision: player.snapshot.queue_revision };
	event.dataTransfer.effectAllowed = "move";
	event.dataTransfer.setData("text/plain", id);
}

async function drop(before: string | null) {
	const moving = dragging.value;
	dragging.value = null;
	dropTarget.value = null;
	if (moving && moving.id !== before && (await player.move(moving.id, before, moving.revision)))
		feedback.value = "Queue order updated.";
}

async function clearQueue() {
	const revision = clearing.value;
	clearing.value = null;
	if (
		revision !== null &&
		(await player.mutate("/api/queue/clear", "POST", { expected_queue_revision: revision }))
	)
		feedback.value = "Queue cleared.";
}
</script>

<template>
	<section aria-labelledby="queue-heading" class="queue-section min-w-0">
		<div class="mb-5 flex items-center justify-between gap-3">
			<div class="flex items-baseline gap-3">
				<h2
					id="queue-heading"
					class="text-xl font-semibold tracking-[-0.025em] text-highlighted"
				>
					Up next
				</h2>
				<span class="text-sm tabular-nums text-muted"
					>{{ queue.length }} {{ queue.length === 1 ? "track" : "tracks" }}</span
				>
			</div>
			<UButton
				label="Clear queue"
				:icon="icons.trash"
				size="sm"
				color="neutral"
				variant="ghost"
				:disabled="!player.enabled || !queue.length"
				@click="clearing = player.snapshot!.queue_revision"
			/>
		</div>
		<form class="mb-5" @submit.prevent="add">
			<label for="youtube-link" class="sr-only">Add a track</label>
			<div class="flex gap-2">
				<UInput
					id="youtube-link"
					v-model="source"
					type="url"
					:icon="icons.link"
					placeholder="Paste a YouTube link"
					autocomplete="off"
					class="min-w-0 flex-1"
					size="xl"
					variant="subtle"
					:disabled="!player.enabled"
					:aria-invalid="!!inputError"
					aria-describedby="link-help"
					@update:model-value="
						inputError = '';
						feedback = '';
					"
				/>
				<UButton
					type="submit"
					label="Add track"
					:icon="icons.plus"
					size="lg"
					color="neutral"
					variant="soft"
					class="shrink-0 justify-center"
					:loading="adding"
					:disabled="!player.enabled || !source.trim()"
				/>
			</div>
			<p
				id="link-help"
				class="mt-2 text-xs"
				:class="inputError ? 'text-error' : 'sr-only'"
				:role="inputError ? 'alert' : undefined"
			>
				{{ inputError || "YouTube and YouTube Music video links" }}
			</p>
		</form>
		<div
			v-if="!player.snapshot && player.connection === 'connecting'"
			class="space-y-3"
			aria-label="Loading queue"
			aria-busy="true"
		>
			<USkeleton v-for="row in 3" :key="row" class="h-18 w-full" />
		</div>
		<template v-else-if="queue.length">
			<div class="queue-columns queue-grid text-xs text-muted" aria-hidden="true">
				<span /><span /><span>Track</span><span>Added by</span><span>Duration</span><span />
			</div>
			<ol aria-label="Upcoming tracks" class="queue-list">
				<li
					v-for="(entry, index) in queue"
					:key="entry.id"
					:data-entry-id="entry.id"
					class="queue-row queue-grid"
					:class="{ 'drop-target': dropTarget === entry.id }"
					@dragover.prevent="dragging && (dropTarget = entry.id)"
					@drop.prevent="drop(entry.id)"
				>
					<button
						type="button"
						:draggable="player.enabled"
						:disabled="!player.enabled"
						:aria-label="'Drag ' + trackTitle(entry) + ' to reorder'"
						class="queue-handle relative size-8 cursor-grab items-center justify-center text-muted"
						tabindex="-1"
						@dragstart="startDrag($event, entry.id)"
						@dragend="
							dragging = null;
							dropTarget = null;
						"
					>
						<span class="queue-number text-xs tabular-nums">{{
							String(index + 1).padStart(2, "0")
						}}</span>
						<UIcon :name="icons.drag" class="queue-grip absolute size-4" />
					</button>
					<PlayerTrackArtwork :entry="entry" class="queue-cover" />
					<div class="queue-title min-w-0">
						<a
							:href="entry.source_url"
							target="_blank"
							rel="noopener noreferrer"
							class="block truncate text-sm font-medium text-highlighted hover:underline"
							:title="trackTitle(entry)"
							>{{ trackTitle(entry) }}</a
						>
						<p class="mt-1 truncate text-xs text-muted">
							<PlayerArtistLink :entry="entry" />
						</p>
					</div>
					<PlayerContributor :contributor="entry.added_by" compact class="queue-person" />
					<div class="queue-timing text-xs text-muted">
						<span class="tabular-nums">{{ formatTime(entry.duration_seconds) }}</span>
						<span
							v-if="waits[index] != null"
							class="queue-wait"
							title="Estimated start, assuming the queue stays in this order"
							>{{ formatWait(waits[index]!) }}</span
						>
					</div>
					<UDropdownMenu
						:items="[
							{
								label: 'Move up',
								icon: icons.arrowUp,
								disabled: !player.enabled || index === 0,
								onSelect: () => move(index, -1),
							},
							{
								label: 'Move down',
								icon: icons.arrowDown,
								disabled: !player.enabled || index === queue.length - 1,
								onSelect: () => move(index, 1),
							},
							{
								label: 'Remove',
								icon: icons.trash,
								color: 'error',
								disabled: !player.enabled,
								onSelect: () => player.mutate('/api/queue/' + entry.id, 'DELETE'),
							},
						]"
						:content="{ align: 'end' }"
					>
						<UButton
							:icon="icons.ellipsis"
							:aria-label="'Options for ' + trackTitle(entry)"
							color="neutral"
							variant="ghost"
							class="queue-menu size-10 justify-center"
						/>
					</UDropdownMenu>
				</li>
			</ol>
		</template>
		<div v-else class="queue-empty flex items-start gap-4 py-9">
			<UIcon :name="icons.music" class="mt-1 size-7 shrink-0 text-primary" />
			<div>
				<h3 class="font-medium text-highlighted">
					{{
						player.connection === "live"
							? "Room for your next favourite"
							: "Waiting for the bot"
					}}
				</h3>
				<p class="mt-2 text-sm text-muted">
					{{
						player.connection === "live"
							? "Drop a link above. We'll take it from there."
							: "Your queue will appear when the connection is back."
					}}
				</p>
			</div>
		</div>
		<div
			v-if="dragging"
			class="my-3 rounded-lg border border-dashed border-default p-4 text-center text-sm text-muted"
			@dragover.prevent
			@drop.prevent="drop(null)"
		>
			Drop here to move to the end
		</div>
		<p role="status" aria-live="polite" class="mt-3 text-xs text-muted">{{ feedback }}</p>
		<UModal
			:open="clearing !== null"
			title="Clear the queue?"
			description="This removes all upcoming tracks. The current track keeps playing."
			@update:open="!$event && (clearing = null)"
		>
			<template #footer>
				<UButton
					label="Cancel"
					color="neutral"
					variant="outline"
					@click="clearing = null"
				/>
				<UButton
					label="Clear queue"
					color="error"
					:disabled="!player.enabled"
					@click="clearQueue"
				/>
			</template>
		</UModal>
	</section>
</template>

<style scoped>
.queue-section {
	max-width: 76rem;
	margin-inline: auto;
	padding-top: 0.5rem;
}
.queue-grid {
	display: grid;
	grid-template-columns: 2rem 3rem minmax(0, 1fr) 10rem 6rem 2.5rem;
	align-items: center;
	column-gap: 1rem;
	padding-inline: 0.5rem;
}
.queue-columns {
	padding-block: 0.75rem;
}
.queue-columns > :nth-child(5),
.queue-timing {
	text-align: right;
}
.queue-row {
	min-height: 4.75rem;
	padding-block: 0.75rem;
	border-bottom: 1px solid var(--ui-border);
}
.queue-row:hover,
.queue-row:focus-within,
.queue-row.drop-target {
	background: var(--ui-bg-muted);
	border-radius: 0.5rem;
}
.queue-handle {
	display: flex;
}
.queue-grip {
	opacity: 0;
}
.queue-row:hover .queue-grip {
	opacity: 1;
}
.queue-row:hover .queue-number {
	opacity: 0;
}
.queue-wait {
	display: block;
	margin-top: 0.35rem;
	font-size: 0.6875rem;
}
.queue-empty {
	border-block: 1px solid var(--ui-border);
}
@container workspace (max-width: 1000px) {
	.queue-grid {
		grid-template-columns: 1.5rem 3rem minmax(0, 1fr) 7.5rem 5rem 2.5rem;
		column-gap: 0.75rem;
	}
}
@container workspace (max-width: 600px) {
	.queue-grid {
		grid-template-columns: 2.75rem minmax(0, 1fr) 2.5rem;
		gap: 0.375rem 0.75rem;
	}
	.queue-columns,
	.queue-handle {
		display: none;
	}
	.queue-cover {
		grid-column: 1;
		grid-row: 1 / span 3;
		align-self: start;
		width: 2.75rem;
		height: 2.75rem;
	}
	.queue-title {
		grid-column: 2;
		grid-row: 1;
	}
	.queue-person {
		grid-column: 2;
		grid-row: 2;
	}
	.queue-timing {
		grid-column: 2;
		grid-row: 3;
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		text-align: left;
	}
	.queue-wait {
		display: inline;
		margin-top: 0;
		font-size: inherit;
	}
	.queue-menu {
		grid-column: 3;
		grid-row: 1 / span 3;
	}
}
</style>
