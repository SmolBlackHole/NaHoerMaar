<script setup lang="ts">
import type { StatisticsPeriod } from "~/core/models/account";
import { formatStatistic, formatStatisticsDuration } from "~/core/models/statistics";

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Overview | NaHörMaar" });
const core = useNuxtApp().$backendCore;
const statistics = core.workflows.statistics();
const recent = core.workflows.recent();
const { icons } = useTheme();
const period = ref<StatisticsPeriod>("7d");
const report = computed(() => statistics.groupReport.data.value);
const recentTracks = computed(() => (recent.recent.data.value?.entries ?? []).slice(0, 5));
const metrics = computed(() => {
	const totals = report.value?.totals;
	return [
		{
			label: "Playback time",
			value: formatStatisticsDuration(totals?.playback_seconds ?? 0),
			icon: icons.value.play,
		},
		{
			label: "Time heard",
			value: formatStatisticsDuration(totals?.listening_seconds ?? 0),
			icon: icons.value.headphones,
		},
		{
			label: "Active listeners",
			value: formatStatistic(report.value?.active_listeners ?? 0),
			icon: icons.value.users,
		},
		{
			label: "Confirmed plays",
			value: formatStatistic(totals?.playback.overall.started ?? 0),
			icon: icons.value.play,
		},
	];
});
const secondaryMetrics = computed(() => {
	const totals = report.value?.totals;
	return [
		{
			label: "Channel presence",
			value: formatStatisticsDuration(totals?.presence_seconds ?? 0),
			icon: icons.value.users,
		},
		{
			label: "Different tracks",
			value: formatStatistic(totals?.unique_tracks ?? 0),
			icon: icons.value.music,
		},
		{
			label: "Different artists",
			value: formatStatistic(totals?.unique_artists ?? 0),
			icon: icons.value.user,
		},
		{
			label: "Average manual wait",
			value:
				totals?.average_wait_seconds == null
					? "No plays yet"
					: formatStatisticsDuration(totals.average_wait_seconds),
			icon: icons.value.clock,
		},
	];
});
const playbackOutcomes = computed(() => {
	const playback = report.value?.totals.playback.overall;
	const items = [
		{
			label: "Completed",
			value: playback?.completed ?? 0,
			barClass: "bg-primary",
		},
		{
			label: "Skipped",
			value: playback?.skipped ?? 0,
			barClass: "bg-warning",
		},
		{
			label: "Stopped",
			value: playback?.stopped ?? 0,
			barClass: "bg-neutral-400 dark:bg-neutral-500",
		},
		{
			label: "Failed",
			value: playback?.failed ?? 0,
			barClass: "bg-error",
		},
	];
	const resolved = items.reduce((total, item) => total + item.value, 0);
	const started = playback?.started ?? 0;
	const open = Math.max(0, started - resolved);

	return {
		items,
		resolved,
		open,
		completionRate: resolved ? Math.round(((playback?.completed ?? 0) / resolved) * 100) : 0,
	};
});

async function load() {
	await Promise.all([statistics.overview(period.value), recent.load(10)]);
}
watch(period, load);
onMounted(load);
onScopeDispose(() => {
	statistics.dispose();
	recent.dispose();
});
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1">
		<UDashboardPanel
			id="overview"
			class="min-h-0 min-w-0 flex-1"
			:ui="{ body: 'pt-4 sm:pt-4' }"
		>
			<template #header>
				<UDashboardNavbar title="Overview">
					<template #leading><UDashboardSidebarCollapse /></template>
					<template #right>
						<PlayerConnection />
						<UButton
							to="/"
							label="Open player"
							:icon="icons.music"
							color="neutral"
							variant="outline"
						/>
					</template>
				</UDashboardNavbar>
			</template>
			<template #body>
				<div class="w-full space-y-8 pb-4 sm:pb-6">
					<div class="flex flex-wrap items-end justify-between gap-4">
						<div>
							<h1 class="text-2xl font-semibold text-highlighted">What's been on</h1>
							<p class="mt-2 text-sm text-muted">
								Playback, requests and the people keeping the queue alive.
							</p>
						</div>
						<StatisticsPeriodSelect v-model="period" />
					</div>

					<div v-if="statistics.groupReport.loading.value && !report" class="min-w-0">
						<StatisticsOverviewSkeleton
							:metrics="metrics"
							:secondary-metrics="secondaryMetrics"
						/>
					</div>

					<div v-else-if="!report" class="grid min-h-80 place-items-center text-center">
						<div class="max-w-sm">
							<UIcon :name="icons.warning" class="mx-auto size-10 text-warning" />
							<h2 class="mt-4 text-lg font-semibold text-highlighted">
								Statistics are unavailable
							</h2>
							<p class="mt-2 text-sm text-muted" role="alert">
								{{ statistics.groupReport.error.value }}
							</p>
							<UButton
								class="mt-5"
								label="Try again"
								:icon="icons.reload"
								color="neutral"
								variant="outline"
								@click="load"
							/>
						</div>
					</div>

					<template v-else>
						<StatisticsMetricGrid :items="metrics" />
						<p
							v-if="report.coverage.partial"
							class="text-xs leading-relaxed text-muted"
						>
							This period starts before NaHörMaar's recorded history. Earlier activity
							is not included.
						</p>

						<section aria-labelledby="listeners-heading">
							<h2
								id="listeners-heading"
								class="text-lg font-semibold text-highlighted"
							>
								Top listeners
							</h2>
							<p class="mt-1 text-xs text-muted">
								Who spent the most time listening in Discord.
							</p>
							<div
								v-if="report.top_listeners.length"
								class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
							>
								<NuxtLink
									v-for="listener in report.top_listeners"
									:key="listener.user_id"
									:to="`/profile/${listener.user_id}`"
									class="group flex items-center gap-3 rounded-xl bg-elevated/40 p-4 transition-colors hover:bg-elevated/70"
								>
									<UAvatar
										:src="listener.avatar_url"
										:alt="listener.display_name ?? 'Listener'"
										size="lg"
									/>
									<div class="min-w-0 flex-1">
										<p class="truncate text-sm font-medium text-highlighted">
											{{
												listener.display_name ??
												listener.discord_display_name ??
												listener.discord_username ??
												"Listener"
											}}
										</p>
										<p class="mt-1 truncate text-xs text-muted">
											{{
												formatStatisticsDuration(listener.listening_seconds)
											}}
											heard
										</p>
									</div>
									<UIcon
										:name="icons.arrowRight"
										class="size-4 shrink-0 text-dimmed transition-transform group-hover:translate-x-0.5"
									/>
								</NuxtLink>
							</div>
							<p v-else class="mt-4 text-sm text-muted">
								Listening time appears once people join the channel.
							</p>
						</section>

						<div class="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(18rem,1fr)]">
							<StatisticsActivity :activity="report.activity" />
							<section aria-labelledby="request-origin-heading">
								<h2
									id="request-origin-heading"
									class="text-lg font-semibold text-highlighted"
								>
									Where requests came from
								</h2>
								<p class="mt-1 text-xs text-muted">
									Manual choices and Radio stay separate.
								</p>
								<dl class="mt-5 space-y-1 rounded-xl bg-elevated/40 p-2">
									<div
										class="flex items-center justify-between gap-4 rounded-lg px-3 py-2.5"
									>
										<dt class="text-sm text-muted">Manual</dt>
										<dd class="font-medium tabular-nums text-highlighted">
											{{ formatStatistic(report.totals.requests.manual) }}
										</dd>
									</div>
									<div
										class="flex items-center justify-between gap-4 rounded-lg px-3 py-2.5"
									>
										<dt class="text-sm text-muted">Radio</dt>
										<dd class="font-medium tabular-nums text-highlighted">
											{{ formatStatistic(report.totals.requests.radio) }}
										</dd>
									</div>
								</dl>
							</section>
						</div>

						<div class="grid min-w-0 gap-10 xl:grid-cols-2">
							<section class="min-w-0" aria-labelledby="tracks-heading">
								<h2
									id="tracks-heading"
									class="text-lg font-semibold text-highlighted"
								>
									Most played tracks
								</h2>
								<ol v-if="report.top_tracks.length" class="mt-4 space-y-2">
									<li
										v-for="track in report.top_tracks"
										:key="track.track_id"
										class="flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-elevated/50"
									>
										<PlayerTrackArtwork
											:entry="{ artwork_url: track.artwork_url }"
											class="size-10 shrink-0"
										/>
										<div class="min-w-0 flex-1">
											<p
												class="truncate text-sm font-medium text-highlighted"
											>
												{{ track.title }}
											</p>
											<p class="mt-0.5 truncate text-xs text-muted">
												{{
													track.artist_names.join(", ") ||
													"Unknown artist"
												}}
											</p>
										</div>
										<span class="shrink-0 text-xs tabular-nums text-muted"
											>{{ track.plays }} plays</span
										>
									</li>
								</ol>
								<p v-else class="mt-4 text-sm text-muted">
									No confirmed tracks in this period.
								</p>
							</section>
							<section class="min-w-0" aria-labelledby="artists-heading">
								<h2
									id="artists-heading"
									class="text-lg font-semibold text-highlighted"
								>
									On repeat
								</h2>
								<ol v-if="report.top_artists.length" class="mt-4 space-y-2">
									<li
										v-for="artist in report.top_artists"
										:key="artist.artist_id"
										class="flex items-center justify-between gap-4 rounded-lg px-2 py-2 transition-colors hover:bg-elevated/50"
									>
										<span
											class="truncate text-sm font-medium text-highlighted"
											>{{ artist.name }}</span
										>
										<span class="shrink-0 text-xs tabular-nums text-muted"
											>{{ artist.plays }} plays</span
										>
									</li>
								</ol>
								<p v-else class="mt-4 text-sm text-muted">
									No artists in this period.
								</p>
							</section>
						</div>

						<StatisticsMetricGrid :items="secondaryMetrics" />

						<section aria-labelledby="outcomes-heading">
							<div class="flex flex-wrap items-end justify-between gap-3">
								<div>
									<h2
										id="outcomes-heading"
										class="text-lg font-semibold text-highlighted"
									>
										Playback outcomes
									</h2>
									<p class="mt-1 text-xs text-muted">
										How resolved plays ended in this period.
									</p>
								</div>
								<p class="text-sm text-muted">
									<span class="font-semibold tabular-nums text-highlighted">
										{{ playbackOutcomes.completionRate }}%
									</span>
									completed
								</p>
							</div>

							<div class="mt-4 rounded-xl bg-elevated/40 p-4 sm:p-5">
								<div
									class="flex h-2 overflow-hidden rounded-full bg-muted"
									role="img"
									:aria-label="`${playbackOutcomes.completionRate}% of resolved plays completed`"
								>
									<div
										v-for="outcome in playbackOutcomes.items.filter(
											(item) => item.value > 0,
										)"
										:key="outcome.label"
										:class="outcome.barClass"
										:style="{
											width: `${
												playbackOutcomes.resolved
													? (outcome.value / playbackOutcomes.resolved) *
														100
													: 0
											}%`,
										}"
									/>
								</div>
								<dl class="mt-4 grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-4">
									<div
										v-for="outcome in playbackOutcomes.items"
										:key="outcome.label"
									>
										<dt class="flex items-center gap-2 text-xs text-muted">
											<span
												:class="outcome.barClass"
												class="size-2 rounded-full"
											/>
											{{ outcome.label }}
										</dt>
										<dd
											class="mt-1 text-lg font-semibold tabular-nums text-highlighted"
										>
											{{ formatStatistic(outcome.value) }}
										</dd>
									</div>
								</dl>
								<p v-if="playbackOutcomes.open" class="mt-4 text-xs text-muted">
									{{ formatStatistic(playbackOutcomes.open) }} started
									{{ playbackOutcomes.open === 1 ? "play is" : "plays are" }}
									still in progress or awaiting an outcome.
								</p>
							</div>
						</section>

						<section>
							<div class="mb-3 flex items-center justify-between gap-4">
								<h2 class="text-lg font-semibold text-highlighted">
									Recently played
								</h2>
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
	</div>
</template>
