<script setup lang="ts">
import { formatTime, trackTitle, youtubeVideoId } from "#shared/player";
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
		if (await player.mutate("/api/queue", "POST", { source_url: submitted })) {
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
	<section
		aria-labelledby="queue-heading"
		class="min-w-0 border-t border-default lg:border-t-0 lg:border-l"
	>
		<div class="space-y-5 p-5 sm:p-8">
			<div class="flex items-center justify-between gap-3">
				<div class="flex items-center gap-3">
					<h2 id="queue-heading" class="text-highlighted text-lg font-semibold">
						Up next
					</h2>
					<UBadge :label="queue.length" color="neutral" variant="subtle" />
				</div>
				<UButton
					label="Clear queue"
					:icon="icons.trash"
					color="neutral"
					variant="ghost"
					:disabled="!player.enabled || !queue.length"
					@click="clearing = player.snapshot!.queue_revision"
				/>
			</div>
			<form class="space-y-2" @submit.prevent="add">
				<label for="youtube-link" class="text-sm font-medium">Add a track</label>
				<div class="flex flex-col gap-2 sm:flex-row">
					<UInput
						id="youtube-link"
						v-model="source"
						type="url"
						:icon="icons.link"
						placeholder="Paste a YouTube link"
						autocomplete="off"
						class="min-w-0 flex-1"
						size="lg"
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
						label="Add to queue"
						:icon="icons.plus"
						size="lg"
						class="justify-center"
						:loading="adding"
						:disabled="!player.enabled || !source.trim()"
					/>
				</div>
				<p
					id="link-help"
					class="text-xs"
					:class="inputError ? 'text-error' : 'text-muted'"
					:role="inputError ? 'alert' : undefined"
				>
					{{ inputError || "YouTube and YouTube Music video links" }}
				</p>
			</form>
		</div>
		<div
			v-if="!player.snapshot && player.connection === 'connecting'"
			class="space-y-4 px-5 sm:px-8"
			aria-label="Loading queue"
			aria-busy="true"
		>
			<USkeleton v-for="row in 3" :key="row" class="h-16 w-full" />
		</div>
		<ol
			v-else-if="queue.length"
			aria-label="Upcoming tracks"
			class="divide-y divide-default border-y border-default"
		>
			<li
				v-for="(entry, index) in queue"
				:key="entry.id"
				:data-entry-id="entry.id"
				class="queue-row flex flex-wrap items-center gap-3 px-5 py-4 sm:px-8"
				:class="dropTarget === entry.id && 'bg-accented'"
				@dragover.prevent="dragging && (dropTarget = entry.id)"
				@drop.prevent="drop(entry.id)"
			>
				<button
					type="button"
					:draggable="player.enabled"
					:disabled="!player.enabled"
					:aria-label="`Drag ${trackTitle(entry)} to reorder`"
					class="text-muted hidden size-8 shrink-0 cursor-grab items-center justify-center rounded-md lg:flex"
					tabindex="-1"
					@dragstart="startDrag($event, entry.id)"
					@dragend="
						dragging = null;
						dropTarget = null;
					"
				>
					<UIcon :name="icons.drag" class="size-4" />
				</button>
				<span class="text-muted w-5 shrink-0 text-right text-xs tabular-nums">{{
					index + 1
				}}</span>
				<PlayerTrackArtwork :entry="entry" />
				<div class="min-w-0 flex-1 basis-24">
					<a
						:href="entry.source_url"
						target="_blank"
						rel="noopener noreferrer"
						class="text-highlighted block truncate text-sm font-medium hover:underline"
						:title="trackTitle(entry)"
						>{{ trackTitle(entry) }}</a
					>
					<p class="text-muted mt-1 truncate text-xs">
						<PlayerArtistLink :entry="entry" />
					</p>
				</div>
				<span class="text-muted text-xs tabular-nums">{{
					formatTime(entry.duration_seconds)
				}}</span>
				<div class="ml-auto flex items-center gap-1">
					<UButton
						:icon="icons.arrowUp"
						:aria-label="`Move ${trackTitle(entry)} up`"
						color="neutral"
						variant="ghost"
						class="size-9 justify-center"
						:disabled="!player.enabled || index === 0"
						@click="move(index, -1)"
					/>
					<UButton
						:icon="icons.arrowDown"
						:aria-label="`Move ${trackTitle(entry)} down`"
						color="neutral"
						variant="ghost"
						class="size-9 justify-center"
						:disabled="!player.enabled || index === queue.length - 1"
						@click="move(index, 1)"
					/>
					<UButton
						:icon="icons.close"
						:aria-label="`Remove ${trackTitle(entry)}`"
						color="neutral"
						variant="ghost"
						class="size-9 justify-center"
						:disabled="!player.enabled"
						@click="player.mutate(`/api/queue/${entry.id}`, 'DELETE')"
					/>
				</div>
			</li>
		</ol>
		<div v-else class="flex flex-col items-center gap-3 px-8 py-16 text-center">
			<UIcon :name="icons.music" class="text-muted size-10" />
			<h3 class="text-highlighted font-medium">
				{{ player.connection === "live" ? "The queue is yours" : "Waiting for the bot" }}
			</h3>
			<p class="text-muted max-w-xs text-sm">
				{{
					player.connection === "live"
						? "Paste a link above to line up the next track."
						: "Your queue will appear when the connection is back."
				}}
			</p>
		</div>
		<div
			v-if="dragging"
			class="text-muted m-4 rounded-md border border-dashed border-default p-4 text-center text-sm"
			@dragover.prevent
			@drop.prevent="drop(null)"
		>
			Drop here to move to the end
		</div>
		<p role="status" aria-live="polite" class="text-muted px-5 py-3 text-xs sm:px-8">
			{{ feedback }}
		</p>
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
