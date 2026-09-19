<script setup lang="ts">
import { formatTime, trackTitle, type RecentTrack } from "#shared/player";
import { usePlayerStore } from "~/stores/player";
defineProps<{ entries: RecentTrack[] }>();
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
async function requeue(item: RecentTrack) {
	feedback.value = "";
	if (await player.add(item.entry.source_url))
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
					<p class="text-muted mt-1 flex items-center gap-3 text-xs">
						<PlayerArtistLink :entry="item.entry" class="truncate" />
						<span
							v-if="item.play_count > 1"
							class="shrink-0 tabular-nums"
							title="Playback starts in the last 100 history entries"
							>{{ item.play_count }} plays</span
						>
					</p>
				</div>
				<span class="text-muted text-xs tabular-nums">{{
					formatTime(item.entry.duration_seconds)
				}}</span>
				<time
					:datetime="item.played_at"
					class="text-muted hidden text-xs tabular-nums lg:block lg:w-36"
					>{{ playedAt(item.played_at) }}</time
				>
				<UButton
					:icon="icons.plus"
					color="neutral"
					variant="ghost"
					:aria-label="`Queue ${trackTitle(item.entry)} again`"
					:title="`Queue ${trackTitle(item.entry)} again`"
					class="ml-auto size-10 shrink-0 justify-center"
					:disabled="!player.enabled"
					@click="requeue(item)"
				/>
			</li>
		</ol>
		<p v-else class="py-6 text-sm text-muted">Tracks appear here once they start playing.</p>
		<p role="status" class="text-muted min-h-6 text-xs">{{ feedback }}</p>
	</div>
</template>
