<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import Sortable, { type SortableEvent } from "sortablejs";
import { formatTime, trackSource, type Contributor, type QueueEntry } from "~/core/models/player";

const core = useNuxtApp().$backendCore;
const player = core.stores.usePlayerStore();
const session = core.stores.useSessionStore();
const { icons } = useTheme();
const { position } = usePlaybackPosition();
const toast = useToast();
const queue = computed(() => player.state?.queue ?? []);
const mine = computed(() =>
	queue.value.filter(({ request }) => request.requested_by === session.account?.user_id),
);
type ClearTarget = {
	requestedBy: string | null;
	name: string | null;
	count: number;
	mine: boolean;
};
const clearing = ref<ClearTarget | null>(null);
const placing = ref<{ id: string; position: number; ids: string[] } | null>(null);
const radioEntry = ref<QueueEntry | null>(null);
const list = ref<HTMLElement | null>(null);
let sortable: Sortable | null = null;
let drag: { id: string; revision: number; ids: string[] } | null = null;

const otherContributors = computed(() => {
	const contributors = new Map<string, { contributor: Contributor; count: number }>();
	for (const { request } of queue.value) {
		const contributor = request.contributor;
		const userId = request.requested_by;
		if (!contributor || !userId || userId === session.account?.user_id) continue;
		const existing = contributors.get(userId);
		if (existing) existing.count += 1;
		else contributors.set(userId, { contributor, count: 1 });
	}
	return [...contributors.values()].sort((left, right) =>
		left.contributor.display_name.localeCompare(right.contributor.display_name),
	);
});
function confirmClear(requestedBy: string | null, name: string | null, mine = false) {
	const count = requestedBy
		? queue.value.filter(({ request }) => request.requested_by === requestedBy).length
		: queue.value.length;
	if (count) clearing.value = { requestedBy, name, count, mine };
}
const removalItems = computed<DropdownMenuItem[][]>(() => {
	const groups: DropdownMenuItem[][] = [
		[
			{
				label: `Remove my tracks (${mine.value.length})`,
				icon: icons.value.user,
				disabled: !player.canControl || !mine.value.length,
				onSelect: () => confirmClear(session.account?.user_id ?? null, null, true),
			},
		],
	];
	if (otherContributors.value.length)
		groups.push([
			{ type: "label", label: "Remove by person" },
			...otherContributors.value.map(({ contributor, count }) => ({
				label: `${contributor.display_name} (${count})`,
				icon: icons.value.user,
				disabled: !player.canControl,
				onSelect: () => confirmClear(contributor.user_id, contributor.display_name),
			})),
		]);
	groups.push([
		{
			label: `Clear entire queue (${queue.value.length})`,
			icon: icons.value.trash,
			color: "error",
			disabled: !player.canControl || !queue.value.length,
			onSelect: () => confirmClear(null, null),
		},
	]);
	return groups;
});
const clearTitle = computed(() => {
	const target = clearing.value;
	if (!target) return "";
	const tracks = `${target.count} ${target.count === 1 ? "track" : "tracks"}`;
	if (target.mine) return `Remove your ${tracks}?`;
	return target.name ? `Remove ${tracks} added by ${target.name}?` : `Clear all ${tracks}?`;
});
const clearDescription = computed(() => {
	const target = clearing.value;
	const scope = target?.mine
		? "Only your upcoming tracks will be removed."
		: target?.name
			? `Only upcoming tracks added by ${target.name} will be removed.`
			: "This removes everyone’s upcoming tracks.";
	return `${scope} The current track keeps playing. You can undo the removal from the notification.`;
});
const clearAction = computed(() => {
	if (clearing.value?.mine) return "Remove my tracks";
	return clearing.value?.name ? "Remove tracks" : "Clear entire queue";
});

function moveTarget(ids: string[], entryId: string, position: number): string | null | undefined {
	const remaining = ids.filter((id) => id !== entryId);
	const index = Math.max(0, Math.min(remaining.length, position - 1));
	const before = remaining[index] ?? null;
	const original = ids.indexOf(entryId);
	const currentBefore = ids[original + 1] ?? null;
	return before === currentBefore ? undefined : before;
}

async function move(index: number, direction: -1 | 1) {
	const entry = queue.value[index];
	if (!entry) return;
	const before = queue.value[index + (direction === -1 ? -1 : 2)]?.id ?? null;
	await player.move(entry.id, before);
}

async function finishDrag(event: SortableEvent) {
	const moving = drag;
	if (!moving) return;
	sortable?.sort(moving.ids);
	drag = null;
	if (
		event.newDraggableIndex === undefined ||
		event.newDraggableIndex === event.oldDraggableIndex
	)
		return;
	if (player.state?.queue_revision !== moving.revision) {
		toast.add({
			title: "The queue changed while you were dragging",
			description: "Try moving the track again.",
			color: "warning",
		});
		return;
	}
	const before = moveTarget(moving.ids, moving.id, event.newDraggableIndex + 1);
	if (before !== undefined) await player.move(moving.id, before);
}

onMounted(() => {
	watch(
		list,
		(element) => {
			sortable?.destroy();
			sortable = element
				? new Sortable(element, {
						draggable: ".queue-row",
						handle: ".queue-handle",
						dataIdAttr: "data-entry-id",
						animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches
							? 0
							: 160,
						ghostClass: "queue-placeholder",
						chosenClass: "queue-chosen",
						forceFallback: true,
						fallbackTolerance: 5,
						delay: 150,
						delayOnTouchOnly: true,
						touchStartThreshold: 4,
						disabled: !player.canControl,
						onStart: ({ item }) => {
							const id = item.dataset.entryId;
							if (!id || !player.state) return;
							drag = {
								id,
								revision: player.state.queue_revision,
								ids: queue.value.map((entry) => entry.id),
							};
						},
						onEnd: (event) => void finishDrag(event),
					})
				: null;
		},
		{ immediate: true, flush: "post" },
	);
});
watch(
	() => player.canControl,
	(enabled) => sortable?.option("disabled", !enabled),
);
onBeforeUnmount(() => sortable?.destroy());

function choosePosition(entry: QueueEntry, index: number) {
	placing.value = {
		id: entry.id,
		position: index + 1,
		ids: queue.value.map(({ id }) => id),
	};
}
async function submitPosition() {
	const target = placing.value;
	if (!target) return;
	const before = moveTarget(target.ids, target.id, target.position);
	if (before !== undefined) await player.move(target.id, before);
	placing.value = null;
}
async function clearQueue() {
	const target = clearing.value;
	clearing.value = null;
	if (target) await player.clear(target.requestedBy);
}
async function startRadio(entry: QueueEntry) {
	const sourceId = entry.request.source_id ?? entry.request.track.sources[0]?.id;
	if (!sourceId) return null;
	return player.startRadio({
		kind: "track",
		track_source_id: sourceId,
		discovery_snapshot_id: null,
	});
}
function requestRadio(entry: QueueEntry) {
	if (player.state?.radio) radioEntry.value = entry;
	else void startRadio(entry);
}
async function confirmRadio() {
	const entry = radioEntry.value;
	if (entry && (await startRadio(entry))) radioEntry.value = null;
}
function waitUntil(index: number) {
	const runtime = player.state?.runtime;
	const currentRemaining = runtime?.duration_seconds
		? Math.max(0, runtime.duration_seconds - position.value)
		: 0;
	return queue.value
		.slice(0, index)
		.reduce(
			(total, entry) => total + (entry.request.track.duration_seconds ?? 0),
			currentRemaining,
		);
}
function formatWait(seconds: number) {
	if (seconds < 60) return "<1 min";
	const minutes = Math.round(seconds / 60);
	return minutes < 60 ? `~${minutes} min` : `~${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
</script>

<template>
	<section aria-labelledby="queue-heading" class="queue-section min-w-0">
		<PlayerDiscovery />
		<PlayerRadioStatus />
		<div class="queue-heading">
			<div class="queue-heading-label flex items-baseline gap-3">
				<h2
					id="queue-heading"
					class="text-xl font-semibold tracking-tight text-highlighted"
				>
					Up next
				</h2>
				<span class="text-sm tabular-nums text-muted">
					{{ queue.length }} {{ queue.length === 1 ? "track" : "tracks" }}
				</span>
			</div>
			<UDropdownMenu
				:items="removalItems"
				:content="{ align: 'end' }"
				:ui="{ item: 'min-h-11', content: 'max-w-[calc(100vw-2rem)]' }"
			>
				<UButton
					label="Remove"
					:loading="player.isPending('queue.clear')"
					:aria-busy="player.isPending('queue.clear')"
					:icon="icons.trash"
					:trailing-icon="icons.chevronDown"
					color="neutral"
					variant="ghost"
					class="min-h-11"
					:disabled="!player.canControl || !queue.length"
					aria-label="Remove tracks from the queue"
				/>
			</UDropdownMenu>
		</div>

		<div
			v-if="!player.state && player.connection === 'connecting'"
			class="space-y-3"
			aria-label="Loading queue"
			aria-busy="true"
		>
			<USkeleton v-for="row in 3" :key="row" class="h-18 w-full" />
		</div>
		<template v-else-if="queue.length">
			<div class="queue-columns queue-grid text-xs text-muted" aria-hidden="true">
				<span /><span /><span>Track</span><span>Requested by</span><span>Duration</span
				><span />
			</div>
			<ol ref="list" aria-label="Upcoming tracks" class="queue-list">
				<li
					v-for="(entry, index) in queue"
					:key="entry.id"
					:data-entry-id="entry.id"
					class="queue-row queue-grid"
				>
					<button
						type="button"
						:disabled="!player.canControl"
						:aria-label="`Drag ${entry.request.track.title} to reorder`"
						class="queue-handle relative size-11 cursor-grab items-center justify-center text-muted"
						tabindex="-1"
					>
						<span class="queue-number text-xs tabular-nums">
							{{ String(index + 1).padStart(2, "0") }}
						</span>
						<UIcon :name="icons.drag" class="queue-grip absolute size-4" />
					</button>
					<PlayerTrackArtwork :entry="entry.request.track" class="queue-cover" />
					<div class="queue-title min-w-0">
						<UTooltip :text="`Open ${entry.request.track.title} in a new tab`">
							<a
								:href="trackSource(entry.request)?.source_url"
								target="_blank"
								rel="noopener noreferrer"
								class="block truncate text-sm font-medium text-highlighted hover:underline"
							>
								{{ entry.request.track.title }}
							</a>
						</UTooltip>
						<p class="mt-1 truncate text-xs text-muted">
							<PlayerArtistLink :entry="entry.request.track" />
						</p>
					</div>
					<div class="queue-details">
						<PlayerContributor
							:contributor="entry.request.contributor"
							:origin="entry.request.origin"
							compact
							class="queue-person"
						/>
						<div class="queue-timing text-xs text-muted">
							<span class="tabular-nums">{{
								formatTime(entry.request.track.duration_seconds)
							}}</span>
							<UTooltip
								text="Estimated start, assuming the queue stays in this order"
							>
								<span class="queue-wait">{{ formatWait(waitUntil(index)) }}</span>
							</UTooltip>
						</div>
					</div>
					<UDropdownMenu
						:items="[
							{
								label: 'Start a radio from this track',
								icon: icons.radio,
								disabled: !player.canControl,
								onSelect: () => requestRadio(entry),
							},
							{
								label: 'Move to position…',
								icon: icons.drag,
								disabled: !player.canControl || queue.length < 2,
								onSelect: () => choosePosition(entry, index),
							},
							{
								label: 'Move up',
								icon: icons.arrowUp,
								disabled: !player.canControl || index === 0,
								onSelect: () => move(index, -1),
							},
							{
								label: 'Move down',
								icon: icons.arrowDown,
								disabled: !player.canControl || index === queue.length - 1,
								onSelect: () => move(index, 1),
							},
							{
								label: 'Remove',
								icon: icons.trash,
								color: 'error',
								disabled: !player.canControl,
								onSelect: () => player.remove(entry.id),
							},
						]"
						:content="{ align: 'end' }"
					>
						<UTooltip :text="`Options for ${entry.request.track.title}`">
							<UButton
								:icon="icons.ellipsis"
								:loading="
									player.isPending('queue.remove') ||
									player.isPending('queue.move')
								"
								:aria-label="`Options for ${entry.request.track.title}`"
								color="neutral"
								variant="ghost"
								class="queue-menu size-11 justify-center"
							/>
						</UTooltip>
					</UDropdownMenu>
					<form
						v-if="placing?.id === entry.id"
						class="queue-placement"
						@submit.prevent="submitPosition"
						@keydown.esc="placing = null"
					>
						<label :for="`position-${entry.id}`" class="text-xs text-muted"
							>Position</label
						>
						<input
							:id="`position-${entry.id}`"
							v-model.number="placing.position"
							class="queue-position-input"
							type="number"
							min="1"
							:max="placing.ids.length"
							step="1"
							required
							:disabled="!player.canControl"
						/>
						<UButton
							type="submit"
							label="Move"
							:loading="player.isPending('queue.move')"
							color="neutral"
							:disabled="!player.canControl"
						/>
						<UButton
							label="Cancel"
							color="neutral"
							variant="ghost"
							@click="placing = null"
						/>
					</form>
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
							? "Search for a track or paste a YouTube link above."
							: "Your queue will appear when the connection is back."
					}}
				</p>
			</div>
		</div>

		<UModal
			:open="clearing !== null"
			:ui="{ footer: 'justify-end' }"
			:title="clearTitle"
			:description="clearDescription"
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
					:label="clearAction"
					color="error"
					:disabled="!player.canControl"
					:loading="player.isPending('queue.clear')"
					@click="clearQueue"
				/>
			</template>
		</UModal>
		<PlayerRadioReplaceConfirmation
			:open="radioEntry !== null"
			:busy="player.isPending('radio.start')"
			@update:open="!$event && (radioEntry = null)"
			@confirm="confirmRadio"
		/>
	</section>
</template>

<style scoped>
.queue-section {
	width: 100%;
	padding-top: 0.5rem;
}
.queue-heading {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 0.75rem;
	margin-bottom: 0.75rem;
}
.queue-details {
	display: contents;
}
.queue-grid {
	display: grid;
	grid-template-columns: 2.75rem 3rem minmax(0, 1fr) 10rem 6rem 2.75rem;
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
	border-radius: 0.5rem;
	transition: background-color 140ms ease-out;
}
.queue-row:hover,
.queue-row:focus-within {
	background: var(--ui-bg-muted);
}
.queue-handle {
	display: flex;
	touch-action: none;
}
.queue-grip {
	opacity: 0;
}
.queue-placeholder {
	background: var(--ui-bg-elevated);
	border-radius: 0.5rem;
}
.queue-placeholder > * {
	opacity: 0.2;
}
.queue-floating {
	background: var(--ui-bg-elevated);
	border-radius: 0.5rem;
	box-shadow: 0 12px 32px rgb(0 0 0 / 25%);
	opacity: 0.97 !important;
}
.queue-chosen .queue-handle {
	cursor: grabbing;
}
.queue-placement {
	grid-column: 1 / -1;
	display: flex;
	align-items: center;
	flex-wrap: wrap;
	gap: 0.75rem;
	padding: 0.75rem 0.5rem 0.25rem;
}
.queue-position-input {
	width: 4.5rem;
	min-height: 2.75rem;
	padding: 0.5rem;
	border-radius: 0.375rem;
	background: var(--ui-bg-elevated);
	color: var(--ui-text-highlighted);
	border: 1px solid var(--ui-border);
}
.queue-position-input:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
@media (hover: hover) and (pointer: fine) {
	.queue-row:hover .queue-grip {
		opacity: 1;
	}
	.queue-row:hover .queue-number {
		opacity: 0;
	}
}
.queue-wait {
	display: block;
	margin-top: 0.35rem;
	font-size: 0.6875rem;
}
.queue-empty {
	padding-block: 2rem;
}
@container workspace (max-width: 1000px) {
	.queue-grid {
		grid-template-columns: 2.75rem 3rem minmax(0, 1fr) 7.5rem 5rem 2.75rem;
		column-gap: 0.75rem;
	}
}
@container workspace (max-width: 600px) {
	.queue-heading-label {
		flex-wrap: wrap;
		gap: 0.125rem 0.75rem;
	}
	.queue-grid {
		grid-template-columns: 2.75rem 2.75rem minmax(0, 1fr) 2.75rem;
		gap: 0.5rem;
		padding-inline: 0;
	}
	.queue-row {
		padding-block: 1rem;
	}
	.queue-columns {
		display: none;
	}
	.queue-grip {
		display: none;
	}
	.queue-row:hover .queue-number {
		opacity: 1;
	}
	.queue-handle {
		grid-column: 1;
		grid-row: 1;
		align-self: start;
	}
	.queue-cover {
		grid-column: 2;
		grid-row: 1;
		align-self: start;
		width: 2.75rem;
		height: 2.75rem;
	}
	.queue-title {
		grid-column: 3 / -1;
		grid-row: 1;
	}
	.queue-title > a {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		white-space: normal;
		overflow-wrap: anywhere;
	}
	.queue-details {
		grid-column: 2 / 4;
		grid-row: 2;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.5rem 0.875rem;
		min-width: 0;
	}
	.queue-person {
		max-width: 100%;
	}
	.queue-timing {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 0.75rem;
		text-align: left;
	}
	.queue-wait {
		display: inline;
		margin-top: 0;
		font-size: inherit;
	}
	.queue-menu {
		grid-column: 4;
		grid-row: 2;
	}
}
</style>
