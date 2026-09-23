<script setup lang="ts">
import { formatTime, trackTitle, type RecentTrack } from "#shared/player";
import { usePlayerStore } from "~/stores/player";
defineProps<{ entries: RecentTrack[] }>();
const player = usePlayerStore();
const { icons } = useTheme();
function playedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}
async function requeue(item: RecentTrack) {
	await player.addMany([item.entry.track_id]);
}
</script>

<template>
	<div>
		<ol v-if="entries.length" aria-label="Recently played tracks" class="space-y-1">
			<li
				v-for="item in entries"
				:key="item.id"
				class="recent-row flex flex-wrap items-center gap-3 py-4"
			>
				<PlayerTrackArtwork :entry="item.entry" class="recent-cover" />
				<div class="recent-track min-w-0 flex-1 basis-32">
					<UTooltip :text="`Open ${trackTitle(item.entry)} in a new tab`">
						<a
							:href="item.entry.source_url"
							target="_blank"
							rel="noopener noreferrer"
							class="recent-title text-highlighted block truncate text-sm font-medium hover:underline"
							>{{ trackTitle(item.entry) }}</a
						>
					</UTooltip>
					<p class="recent-details text-muted mt-1 flex items-center gap-3 text-xs">
						<PlayerArtistLink :entry="item.entry" class="truncate" />
						<span class="recent-mobile-duration shrink-0 tabular-nums">{{
							formatTime(item.entry.duration_seconds)
						}}</span>
						<UTooltip
							v-if="item.play_count > 1"
							text="Playback starts in the last 100 history entries"
						>
							<span class="shrink-0 tabular-nums">{{ item.play_count }} plays</span>
						</UTooltip>
					</p>
				</div>
				<UTooltip
					v-if="item.entry.origin === 'radio'"
					:text="`Radio started by ${item.entry.added_by?.name ?? 'a listener'}`"
				>
					<span class="recent-radio text-xs text-muted"
						>Radio · {{ item.entry.added_by?.name ?? "a listener" }}</span
					>
				</UTooltip>
				<span class="recent-duration text-muted text-xs tabular-nums">{{
					formatTime(item.entry.duration_seconds)
				}}</span>
				<time
					:datetime="item.played_at"
					class="text-muted hidden text-xs tabular-nums lg:block lg:w-36"
					>{{ playedAt(item.played_at) }}</time
				>
				<div class="recent-actions flex items-center ml-auto">
					<UTooltip :text="`Queue ${trackTitle(item.entry)} again`">
						<UButton
							:icon="icons.plus"
							color="neutral"
							variant="ghost"
							:aria-label="`Queue ${trackTitle(item.entry)} again`"
							class="recent-add ml-auto size-11 shrink-0 justify-center"
							:disabled="!player.enabled"
							:loading="player.isAdding(item.entry.track_id)"
							:aria-busy="player.isAdding(item.entry.track_id)"
							@click="requeue(item)"
						/>
					</UTooltip>
					<PlayerRadioAction :entry="item.entry" />
				</div>
			</li>
		</ol>
		<p v-else class="py-6 text-sm text-muted">Tracks appear here once they start playing.</p>
	</div>
</template>

<style scoped>
.recent-row {
	padding-inline-start: 0.75rem;
	border-radius: 0.5rem;
	transition: background-color 140ms ease-out;
}
.recent-row:hover,
.recent-row:focus-within {
	background: var(--ui-bg-muted);
}
.recent-mobile-duration {
	display: none;
}
@container workspace (max-width: 600px) {
	.recent-row {
		display: grid;
		grid-template-columns: 2.75rem minmax(0, 1fr) auto;
		gap: 0.75rem;
		align-items: start;
		padding-block: 1rem;
	}
	.recent-cover {
		width: 2.75rem;
		height: 2.75rem;
	}
	.recent-title {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		white-space: normal;
		overflow-wrap: anywhere;
	}
	.recent-actions {
		grid-column: 3;
		grid-row: 1;
		align-self: center;
	}
	.recent-radio {
		grid-column: 2;
	}
	.recent-details {
		flex-wrap: wrap;
		gap: 0.25rem 0.75rem;
		margin-top: 0.375rem;
	}
	.recent-details > a,
	.recent-details > span:first-child {
		flex-basis: 100%;
	}
	.recent-mobile-duration {
		display: inline;
	}
	.recent-duration,
	.recent-row > time {
		display: none;
	}
	.recent-add {
		align-self: center;
	}
}
</style>
