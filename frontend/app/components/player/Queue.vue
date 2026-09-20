<script setup lang="ts">
import Sortable, { type SortableEvent } from "sortablejs";
import type { DropdownMenuItem } from "@nuxt/ui";
import type { ListenerProfile } from "#shared/profile";
import {
	formatTime,
	formatWait,
	queueMoveTarget,
	queueWaits,
	trackTitle,
	type QueueEntry,
} from "#shared/player";
import { usePlayerStore } from "~/stores/player";
import { useProfileStore } from "~/stores/profile";

const player = usePlayerStore();
const profile = useProfileStore();
const { icons } = useTheme();
const feedback = ref("");
const clearing = ref<{
	revision: number;
	contributor: ListenerProfile | null;
	mine: boolean;
	count: number;
} | null>(null);
const mine = computed(
	() =>
		player.snapshot?.upcoming.filter(
			(entry) => !!profile.profile && entry.added_by?.id === profile.profile.id,
		) ?? [],
);
const contributors = computed(() => {
	const people = new Map<string, { profile: ListenerProfile; count: number }>();
	for (const entry of player.snapshot?.upcoming ?? []) {
		const person = entry.added_by;
		if (!person || person.id === profile.profile?.id) continue;
		const existing = people.get(person.id);
		if (existing) existing.count++;
		else people.set(person.id, { profile: person, count: 1 });
	}
	return [...people.values()].sort((a, b) => a.profile.name.localeCompare(b.profile.name));
});
const removalItems = computed<DropdownMenuItem[][]>(() => {
	const groups: DropdownMenuItem[][] = [
		[
			{
				label: `Remove my tracks (${mine.value.length})`,
				icon: icons.value.user,
				disabled: !player.enabled || !mine.value.length,
				onSelect: () => {
					if (profile.profile) confirmClear(profile.profile);
				},
			},
		],
	];
	if (contributors.value.length)
		groups.push([
			{ type: "label", label: "Remove by person" },
			...contributors.value.map(({ profile: person, count }) => ({
				label: `${person.name} (${count})`,
				avatar: { src: `/avatars/${person.avatar}.png`, alt: "" },
				disabled: !player.enabled,
				onSelect: () => confirmClear(person),
			})),
		]);
	groups.push([
		{
			label: `Clear entire queue (${queue.value.length})`,
			icon: icons.value.trash,
			color: "error",
			disabled: !player.enabled || !queue.value.length,
			onSelect: () => confirmClear(null),
		},
	]);
	return groups;
});
const clearTitle = computed(() => {
	const target = clearing.value;
	if (!target) return "";
	const tracks = `${target.count} ${target.count === 1 ? "track" : "tracks"}`;
	if (target.mine) return `Remove your ${tracks}?`;
	return target.contributor
		? `Remove ${tracks} added by ${target.contributor.name}?`
		: `Clear all ${tracks}?`;
});
const clearDescription = computed(() => {
	const target = clearing.value;
	const scope = target?.mine
		? "Only your upcoming tracks will be removed."
		: target?.contributor
			? `Only upcoming tracks added by ${target.contributor.name} will be removed.`
			: "This removes everyone’s upcoming tracks.";
	return `${scope} The current track keeps playing.`;
});
const dragging = shallowRef<{ id: string; revision: number; entries: QueueEntry[] } | null>(null);
const list = ref<HTMLElement | null>(null);
const queue = shallowRef<QueueEntry[]>([]);
const placing = ref<{ id: string; position: number; revision: number; ids: string[] } | null>(null);
let sortable: Sortable | null = null;
const { now } = usePlaybackPosition();
const waits = computed(() =>
	player.connection === "live" ? queueWaits(player.snapshot, now.value) : [],
);
watch(
	() => player.snapshot?.upcoming,
	(entries) => {
		if (!dragging.value) queue.value = [...(entries ?? [])];
	},
	{ immediate: true },
);

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
						fallbackClass: "queue-floating",
						forceFallback: true,
						fallbackTolerance: 5,
						delay: 150,
						delayOnTouchOnly: true,
						touchStartThreshold: 4,
						disabled: !player.enabled,
						onStart: ({ item }) => {
							if (!player.snapshot || !item.dataset.entryId) return;
							placing.value = null;
							dragging.value = {
								id: item.dataset.entryId,
								revision: player.snapshot.queue_revision,
								entries: [...queue.value],
							};
						},
						onEnd: (event) => {
							void finishDrag(event);
						},
					})
				: null;
		},
		{ immediate: true, flush: "post" },
	);
});
watch(
	() => player.enabled,
	(enabled) => sortable?.option("disabled", !enabled),
);
onBeforeUnmount(() => sortable?.destroy());

async function move(index: number, direction: -1 | 1) {
	const entry = queue.value[index];
	if (!entry || !player.snapshot) return;
	const before = queue.value[index + (direction === -1 ? -1 : 2)]?.id ?? null;
	if (await player.move(entry.id, before, player.snapshot.queue_revision))
		feedback.value = "Queue order updated.";
}

async function finishDrag(event: SortableEvent) {
	const moving = dragging.value;
	if (!moving) return;
	const ids = moving.entries.map((entry) => entry.id);
	// Restore Vue's DOM order before applying the new reactive order.
	sortable?.sort(ids);
	try {
		if (
			event.newDraggableIndex === undefined ||
			event.newDraggableIndex === event.oldDraggableIndex
		)
			return;
		if (player.snapshot?.queue_revision !== moving.revision) {
			feedback.value = "The queue changed while you were dragging. Try again.";
			return;
		}
		const before = queueMoveTarget(ids, moving.id, event.newDraggableIndex + 1);
		if (before === undefined) return;
		const reordered = moving.entries.filter((entry) => entry.id !== moving.id);
		const entry = moving.entries.find((entry) => entry.id === moving.id)!;
		reordered.splice(event.newDraggableIndex, 0, entry);
		queue.value = reordered;
		if (await player.move(moving.id, before, moving.revision))
			feedback.value = "Queue order updated.";
	} finally {
		dragging.value = null;
		queue.value = [...(player.snapshot?.upcoming ?? [])];
	}
}

function choosePosition(id: string, index: number) {
	if (!player.snapshot) return;
	placing.value = {
		id,
		position: index + 1,
		revision: player.snapshot.queue_revision,
		ids: queue.value.map((entry) => entry.id),
	};
}

function focusPosition(event: Event) {
	if (!placing.value) return;
	event.preventDefault();
	void nextTick(() =>
		list.value?.querySelector<HTMLInputElement>(".queue-position-input")?.focus(),
	);
}

async function submitPosition() {
	const target = placing.value;
	if (!target) return;
	if (player.snapshot?.queue_revision !== target.revision) {
		placing.value = null;
		feedback.value = "The queue changed. Choose the position again.";
		return;
	}
	const before = queueMoveTarget(target.ids, target.id, Number(target.position));
	if (before === undefined) return;
	if (await player.move(target.id, before, target.revision)) {
		feedback.value = `Moved to position ${target.position}.`;
		placing.value = null;
	}
}

function confirmClear(contributor: ListenerProfile | null) {
	if (!player.snapshot || !player.enabled) return;
	const count = contributor
		? player.snapshot.upcoming.filter((entry) => entry.added_by?.id === contributor.id).length
		: player.snapshot.upcoming.length;
	if (!count) return;
	clearing.value = {
		revision: player.snapshot.queue_revision,
		contributor,
		mine: !!contributor && contributor.id === profile.profile?.id,
		count,
	};
}

async function clearQueue() {
	const target = clearing.value;
	clearing.value = null;
	if (
		target &&
		(await player.mutate("/api/queue/clear", "POST", {
			expected_queue_revision: target.revision,
			...(target.contributor ? { contributor_id: target.contributor.id } : {}),
		}))
	)
		feedback.value = target.mine
			? "Your upcoming tracks were removed."
			: target.contributor
				? `Upcoming tracks added by ${target.contributor.name} were removed.`
				: "Queue cleared.";
}
</script>

<template>
	<section aria-labelledby="queue-heading" class="queue-section min-w-0">
		<PlayerDiscovery />
		<div class="queue-heading">
			<div class="flex items-baseline gap-3">
				<h2
					id="queue-heading"
					class="text-xl font-semibold tracking-tight text-highlighted"
				>
					Up next
				</h2>
				<span class="text-sm tabular-nums text-muted"
					>{{ queue.length }} {{ queue.length === 1 ? "track" : "tracks" }}</span
				>
			</div>
			<UDropdownMenu
				:items="removalItems"
				:content="{ align: 'end' }"
				:ui="{ item: 'min-h-11', content: 'max-w-[calc(100vw-2rem)]' }"
			>
				<UButton
					label="Remove"
					:icon="icons.trash"
					:trailing-icon="icons.chevronDown"
					color="neutral"
					variant="ghost"
					class="min-h-11"
					:disabled="!player.enabled || !queue.length"
					aria-label="Remove tracks from the queue"
				/>
			</UDropdownMenu>
		</div>
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
			<ol ref="list" aria-label="Upcoming tracks" class="queue-list">
				<li
					v-for="(entry, index) in queue"
					:key="entry.id"
					:data-entry-id="entry.id"
					class="queue-row queue-grid"
				>
					<button
						type="button"
						:disabled="!player.enabled"
						:aria-label="'Drag ' + trackTitle(entry) + ' to reorder'"
						class="queue-handle relative size-11 cursor-grab items-center justify-center text-muted"
						tabindex="-1"
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
							v-if="!dragging && waits[index] != null"
							class="queue-wait"
							title="Estimated start, assuming the queue stays in this order"
							>{{ formatWait(waits[index]!) }}</span
						>
					</div>
					<UDropdownMenu
						:items="[
							{
								label: 'Move to position…',
								icon: icons.drag,
								disabled: !player.enabled || queue.length < 2,
								onSelect: () => choosePosition(entry.id, index),
							},
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
						:content="{ align: 'end', onCloseAutoFocus: focusPosition }"
					>
						<UButton
							:icon="icons.ellipsis"
							:aria-label="'Options for ' + trackTitle(entry)"
							color="neutral"
							variant="ghost"
							class="queue-menu size-11 justify-center"
						/>
					</UDropdownMenu>
					<form
						v-if="placing?.id === entry.id"
						class="queue-placement"
						@submit.prevent="submitPosition"
						@keydown.esc="placing = null"
					>
						<label :for="'position-' + entry.id" class="text-xs text-muted"
							>Position</label
						>
						<input
							:id="'position-' + entry.id"
							v-model.number="placing.position"
							class="queue-position-input"
							type="number"
							min="1"
							:max="placing.ids.length"
							step="1"
							required
							:disabled="!player.enabled"
						/>
						<UButton
							type="submit"
							label="Move"
							color="neutral"
							:disabled="!player.enabled"
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
		<p role="status" aria-live="polite" class="queue-feedback text-xs text-muted">
			{{ feedback }}
		</p>
		<UModal
			:open="clearing !== null"
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
					:label="
						clearing?.mine
							? 'Remove my tracks'
							: clearing?.contributor
								? 'Remove tracks'
								: 'Clear entire queue'
					"
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
.queue-feedback:not(:empty) {
	margin-top: 0.75rem;
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
	border-bottom: 1px solid var(--ui-border);
}
.queue-row:hover,
.queue-row:focus-within {
	background: var(--ui-bg-muted);
	border-radius: 0.5rem;
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
	border-block: 1px solid var(--ui-border);
}
@container workspace (max-width: 1000px) {
	.queue-grid {
		grid-template-columns: 2.75rem 3rem minmax(0, 1fr) 7.5rem 5rem 2.75rem;
		column-gap: 0.75rem;
	}
}
@container workspace (max-width: 600px) {
	.queue-grid {
		grid-template-columns: 2.75rem 2.5rem minmax(0, 1fr) 2.75rem;
		gap: 0.375rem 0.5rem;
		padding-inline: 0;
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
		grid-row: 1 / span 3;
		align-self: start;
	}
	.queue-cover {
		grid-column: 2;
		grid-row: 1 / span 3;
		align-self: start;
		width: 2.5rem;
		height: 2.5rem;
	}
	.queue-title {
		grid-column: 3;
		grid-row: 1;
	}
	.queue-person {
		grid-column: 3;
		grid-row: 2;
	}
	.queue-timing {
		grid-column: 3;
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
		grid-column: 4;
		grid-row: 1 / span 3;
	}
}
</style>
