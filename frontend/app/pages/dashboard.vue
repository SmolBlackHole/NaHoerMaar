<script setup lang="ts">
import { groupHistory, listeningStats } from "#shared/player";
import { usePlayerStore } from "~/stores/player";
useSeoMeta({ title: "Overview | NaHörMaar" });
const player = usePlayerStore();
const { icons } = useTheme();
const days = ref(7);
const now = ref(new Date());
useIntervalFn(() => {
	now.value = new Date();
}, 60000);
const stats = computed(() =>
	listeningStats(player.snapshot?.recently_played ?? [], days.value, now.value),
);
const maxStarts = computed(() => Math.max(1, ...stats.value.buckets.map((day) => day.count)));
const recentTracks = computed(() =>
	groupHistory(player.snapshot?.recently_played ?? []).slice(0, 5),
);
const metrics = computed(() => [
	{ label: "Tracks started", value: stats.value.starts, icon: icons.value.play },
	{ label: "Different tracks", value: stats.value.tracks, icon: icons.value.music },
	{ label: "Artists & channels", value: stats.value.artists, icon: icons.value.user },
	{
		label: "In the queue now",
		value: player.snapshot?.upcoming.length ?? 0,
		icon: icons.value.list,
	},
]);
function dayLabel(date: Date) {
	return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}
</script>

<template>
	<UDashboardPanel id="overview" class="min-w-0">
		<template #header>
			<UDashboardNavbar title="Overview">
				<template #leading><UDashboardSidebarCollapse /></template>
				<template #right
					><UButton
						to="/"
						label="Open player"
						:icon="icons.music"
						color="neutral"
						variant="outline"
				/></template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<div class="space-y-8 py-4 sm:py-6">
				<div class="flex flex-wrap items-end justify-between gap-4">
					<div>
						<h1 class="text-highlighted text-2xl font-semibold">What's been on</h1>
						<p class="text-muted mt-2 text-sm">
							Activity from the last 100 started tracks.
						</p>
					</div>
					<USelect
						v-model="days"
						:items="[
							{ label: 'Last 7 days', value: 7 },
							{ label: 'Last 30 days', value: 30 },
						]"
						aria-label="Activity period"
						class="w-40"
					/>
				</div>
				<div v-if="!player.snapshot" role="status" class="text-muted py-12">
					Waiting for listening activity…
				</div>
				<template v-else>
					<dl class="grid grid-cols-2 gap-4 xl:grid-cols-4">
						<div
							v-for="metric in metrics"
							:key="metric.label"
							class="rounded-xl border border-default p-5"
						>
							<dt class="text-muted flex items-center gap-2 text-sm">
								<UIcon :name="metric.icon" class="size-4 shrink-0" />{{
									metric.label
								}}
							</dt>
							<dd class="text-highlighted mt-4 text-3xl font-semibold tabular-nums">
								{{ metric.value }}
							</dd>
						</div>
					</dl>
					<div class="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(16rem,1fr)]">
						<section aria-labelledby="activity-heading" class="min-w-0">
							<h2
								id="activity-heading"
								class="text-highlighted text-lg font-semibold"
							>
								Daily activity
							</h2>
							<p class="text-muted mt-1 text-xs">
								Playback starts, including skipped tracks
							</p>
							<figure class="mt-8" aria-label="Tracks started per day">
								<div
									class="flex h-44 items-end gap-1 border-b border-default sm:gap-2"
								>
									<div
										v-for="day in stats.buckets"
										:key="day.date.toISOString()"
										class="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-2"
									>
										<span
											v-if="days === 7"
											class="text-muted text-xs tabular-nums"
											>{{ day.count }}</span
										>
										<div
											class="w-full rounded-t-sm bg-primary/75"
											:style="{
												height: `${Math.max(day.count ? 4 : 0, (day.count / maxStarts) * 80)}%`,
											}"
											:title="`${dayLabel(day.date)}: ${day.count} starts`"
										/>
									</div>
								</div>
								<figcaption class="text-muted mt-3 flex justify-between text-xs">
									<span>{{ dayLabel(stats.buckets[0]!.date) }}</span
									><span>{{ dayLabel(stats.buckets.at(-1)!.date) }}</span>
								</figcaption>
								<table class="sr-only">
									<caption>
										Daily playback starts
									</caption>
									<thead>
										<tr>
											<th>Date</th>
											<th>Starts</th>
										</tr>
									</thead>
									<tbody>
										<tr
											v-for="day in stats.buckets"
											:key="day.date.toISOString()"
										>
											<td>{{ dayLabel(day.date) }}</td>
											<td>{{ day.count }}</td>
										</tr>
									</tbody>
								</table>
							</figure>
						</section>
						<section aria-labelledby="artists-heading">
							<h2 id="artists-heading" class="text-highlighted text-lg font-semibold">
								On repeat
							</h2>
							<p class="text-muted mt-1 text-xs">Most played artists and channels</p>
							<ol v-if="stats.topArtists.length" class="mt-5 space-y-5">
								<li
									v-for="[artist, count] in stats.topArtists"
									:key="artist"
									class="space-y-2"
								>
									<div class="flex justify-between gap-3 text-sm">
										<span class="truncate">{{ artist }}</span
										><span class="text-muted tabular-nums">{{ count }}</span>
									</div>
									<div class="h-1 rounded-full bg-elevated">
										<div
											class="h-full rounded-full bg-primary/60"
											:style="{
												width: `${(count / stats.topArtists[0]![1]) * 100}%`,
											}"
										/>
									</div>
								</li>
							</ol>
							<p v-else class="text-muted mt-8 text-sm">
								Play a few tracks to see who's on repeat.
							</p>
						</section>
					</div>
					<section class="border-t border-default pt-6">
						<div class="mb-3 flex items-center justify-between gap-4">
							<h2 class="text-highlighted text-lg font-semibold">Recently played</h2>
							<UButton
								to="/?view=queue#recently-played"
								label="View all"
								variant="link"
								color="neutral"
								:trailing-icon="icons.arrowRight"
							/>
						</div>
						<PlayerRecentList :entries="recentTracks" />
					</section>
				</template>
			</div>
		</template>
	</UDashboardPanel>
</template>
