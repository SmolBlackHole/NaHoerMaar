<script setup lang="ts">
import { formatTime } from "~/core/models/player";
import type { RecentPlayback } from "~/core/models/listening";

defineProps<{ entries: RecentPlayback[] }>();
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const { icons } = useTheme();
function playedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}
function artistUrl(item: RecentPlayback) {
	const artist = item.artist_names.join(", ");
	return artist ? `https://music.youtube.com/search?q=${encodeURIComponent(artist)}` : null;
}
function requeue(item: RecentPlayback) {
	return player.add([{ track_id: item.track_id, source_id: null }]);
}
</script>

<template>
	<div>
		<ol v-if="entries.length" aria-label="Recently played tracks" class="space-y-1">
			<li
				v-for="item in entries"
				:key="item.playback_id"
				class="recent-row flex flex-wrap items-center gap-3 py-4"
			>
				<PlayerTrackArtwork
					:entry="{ artwork_url: item.artwork_url }"
					class="recent-cover"
				/>
				<div class="recent-track min-w-0 flex-1 basis-32">
					<UTooltip v-if="item.source_url" :text="`Open ${item.title} in a new tab`">
						<a
							:href="item.source_url"
							target="_blank"
							rel="noopener noreferrer"
							class="recent-title block truncate text-sm font-medium text-highlighted hover:underline"
						>
							{{ item.title }}
						</a>
					</UTooltip>
					<p
						v-else
						class="recent-title block truncate text-sm font-medium text-highlighted"
					>
						{{ item.title }}
					</p>
					<p class="recent-details mt-1 flex items-center gap-3 text-xs text-muted">
						<a
							v-if="artistUrl(item)"
							:href="artistUrl(item)!"
							target="_blank"
							rel="noopener noreferrer"
							class="truncate hover:text-highlighted hover:underline"
						>
							{{ item.artist_names.join(", ") }}
						</a>
						<span v-else class="truncate">Unknown artist</span>
						<span class="recent-mobile-duration shrink-0 tabular-nums">
							{{ formatTime(item.duration_seconds) }}
						</span>
						<UTooltip
							v-if="item.play_count > 1"
							text="Playback starts in the latest 100 history entries"
						>
							<span class="shrink-0 tabular-nums">{{ item.play_count }} plays</span>
						</UTooltip>
					</p>
				</div>
				<PlayerContributor
					v-if="item.origin === 'radio'"
					:contributor="item.contributor"
					origin="radio"
					class="recent-radio"
				/>
				<span class="recent-duration text-xs tabular-nums text-muted">
					{{ formatTime(item.duration_seconds) }}
				</span>
				<time
					:datetime="item.ended_at ?? item.started_at"
					class="hidden text-xs tabular-nums text-muted lg:block lg:w-36"
				>
					{{ item.ended_at ? playedAt(item.ended_at) : "Playing now" }}
				</time>
				<div class="recent-actions ml-auto flex items-center">
					<UTooltip :text="`Queue ${item.title} again`">
						<UButton
							:icon="icons.plus"
							color="neutral"
							variant="ghost"
							:aria-label="`Queue ${item.title} again`"
							class="recent-add ml-auto size-11 shrink-0 justify-center"
							:disabled="!player.canControl"
							:loading="player.isPending('queue.add')"
							@click="requeue(item)"
						/>
					</UTooltip>
					<PlayerRadioAction
						v-if="item.source_id"
						:source-id="item.source_id"
						:title="item.title"
					/>
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
	.recent-mobile-duration {
		display: inline;
	}
	.recent-duration,
	.recent-row > time {
		display: none;
	}
}
</style>
