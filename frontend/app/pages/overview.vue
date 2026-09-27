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
const recentTracks = computed(() => recent.history.data.value?.entries ?? []);
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
			icon: icons.value.music,
		},
	];
});

async function load() {
	await Promise.all([
		statistics.overview(period.value),
		recent.load({ pageSize: 5, newSnapshot: true }),
	]);
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
				<div class="w-full space-y-10 pb-4 sm:pb-6">
					<div class="flex flex-wrap items-end justify-between gap-4">
						<div>
							<h1 class="text-2xl font-semibold text-highlighted">What's been on</h1>
							<p class="mt-2 text-sm text-muted">
								The time, music and people behind this listening period.
							</p>
						</div>
						<StatisticsPeriodSelect v-model="period" />
					</div>

					<StatisticsOverviewSkeleton
						v-if="statistics.groupReport.loading.value && !report"
						:metrics="metrics"
					/>

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

						<StatisticsListenerLeaderboard
							:listeners="report.top_listeners"
							:total-listening-seconds="report.totals.listening_seconds"
						/>

						<StatisticsActivity :activity="report.activity" />

						<StatisticsMusicStory :report="report" />

						<StatisticsGroupHighlights :report="report" />

						<StatisticsDetails :report="report" />

						<section aria-labelledby="recent-heading">
							<div class="mb-3 flex items-center justify-between gap-4">
								<div>
									<h2
										id="recent-heading"
										class="text-lg font-semibold text-highlighted"
									>
										Recently played
									</h2>
									<p class="mt-1 text-xs text-muted">
										The latest tracks heard by the group.
									</p>
								</div>
								<UButton
									to="/history"
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
