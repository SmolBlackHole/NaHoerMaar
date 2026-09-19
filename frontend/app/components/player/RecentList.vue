<script setup lang="ts">
import { formatTime, trackTitle, type HistoryEntry } from "#shared/player";
import { usePlayerStore } from "~/stores/player";
defineProps<{ entries: HistoryEntry[] }>();
const player = usePlayerStore();
const { icons } = useTheme();
const feedback = ref("");
function playedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}
async function requeue(item: HistoryEntry) {
	feedback.value = "";
	if (await player.mutate("/api/queue", "POST", { source_url: item.entry.source_url }))
		feedback.value = `${trackTitle(item.entry)} added to the queue.`;
}
</script>

<template>
	<div>
		<ol
			v-if="entries.length"
			aria-label="Recently played tracks"
			class="divide-y divide-default"
		>
			<li
				v-for="item in entries"
				:key="item.id"
				class="flex flex-wrap items-center gap-3 py-4"
			>
				<PlayerTrackArtwork :entry="item.entry" />
				<div class="min-w-0 flex-1 basis-32">
					<a
						:href="item.entry.source_url"
						target="_blank"
						rel="noopener noreferrer"
						class="text-highlighted block truncate text-sm font-medium hover:underline"
						:title="trackTitle(item.entry)"
						>{{ trackTitle(item.entry) }}</a
					>
					<p class="text-muted mt-1 truncate text-xs">
						<PlayerArtistLink :entry="item.entry" />
					</p>
				</div>
				<span class="text-muted text-xs tabular-nums">{{
					formatTime(item.entry.duration_seconds)
				}}</span>
				<time
					:datetime="item.played_at"
					class="text-muted order-1 ml-15 text-xs tabular-nums sm:order-none sm:ml-2 sm:w-36"
					>{{ playedAt(item.played_at) }}</time
				>
				<UButton
					label="Queue again"
					:icon="icons.plus"
					color="neutral"
					variant="outline"
					:aria-label="`Queue ${trackTitle(item.entry)} again`"
					class="ml-auto"
					:disabled="!player.enabled"
					@click="requeue(item)"
				/>
			</li>
		</ol>
		<div v-else class="py-16 text-center">
			<UIcon :name="icons.timer" class="text-muted mb-4 size-9" />
			<h2 class="text-highlighted font-medium">Nothing played yet</h2>
			<p class="text-muted mx-auto mt-2 max-w-xs text-sm">
				Started tracks appear here, even if you skip them.
			</p>
			<UButton to="/" label="Open player" class="mt-5" color="neutral" variant="outline" />
		</div>
		<p role="status" class="text-muted min-h-6 text-xs">{{ feedback }}</p>
	</div>
</template>
