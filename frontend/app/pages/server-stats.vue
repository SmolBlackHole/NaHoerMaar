<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Server Stats | NaHörMaar" });

type StatisticsView = "activity" | "music" | "people" | "library";

const core = useNuxtApp().$backendCore;
const statistics = core.workflows.statistics();
const route = useRoute();
const period = useStatisticsPeriod();
const report = computed(() => statistics.groupReport.data.value);
const view = computed<StatisticsView>(() => {
	const value = typeof route.query.view === "string" ? route.query.view : "activity";
	return value === "music" || value === "people" || value === "library" ? value : "activity";
});
const views = [
	{ label: "Activity", value: "activity" as const },
	{ label: "Music", value: "music" as const },
	{ label: "People", value: "people" as const },
	{ label: "Library", value: "library" as const },
];
const copy = computed(() => {
	if (view.value === "music")
		return "Tracks and artists that defined the selected listening period.";
	if (view.value === "people") return "The listeners and shared moments behind the playback.";
	if (view.value === "library")
		return "Reactions and visible shared collections across the group.";
	return "When the group listened and how requests turned into playback.";
});

function load() {
	return statistics.load(period.value);
}

watch(period, load);
onMounted(load);
onScopeDispose(statistics.dispose);
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1">
		<UDashboardPanel
			id="server-stats"
			class="min-h-0 min-w-0 flex-1"
			:ui="{ body: 'pt-4 sm:pt-4' }"
		>
			<template #header>
				<UDashboardNavbar :ui="{ left: 'h-full gap-3 sm:gap-4' }">
					<template #left>
						<UDashboardSidebarCollapse />
						<h1 class="sr-only">Server Stats</h1>
						<div
							class="inline-flex overflow-x-auto rounded-lg bg-elevated p-1"
							role="group"
							aria-label="Server statistics section"
						>
							<UButton
								v-for="item in views"
								:key="item.value"
								:label="item.label"
								:color="view === item.value ? 'primary' : 'neutral'"
								:variant="view === item.value ? 'soft' : 'ghost'"
								:aria-pressed="view === item.value"
								:to="{
									path: '/server-stats',
									query: { ...route.query, view: item.value },
								}"
							/>
						</div>
					</template>
					<template #right><PlayerConnection /></template>
				</UDashboardNavbar>
			</template>

			<template #body>
				<div class="w-full space-y-8 pb-4 sm:pb-6">
					<div class="flex flex-wrap items-end justify-between gap-4">
						<div class="max-w-2xl">
							<p class="text-xs font-medium uppercase tracking-wider text-primary">
								One listening group
							</p>
							<h2 class="mt-2 text-2xl font-semibold text-highlighted">
								Server Stats
							</h2>
							<p class="mt-2 text-sm text-muted">{{ copy }}</p>
						</div>
						<StatisticsPeriodSelect v-model="period" />
					</div>

					<StatisticsServerStatsSkeleton
						v-if="statistics.groupReport.loading.value && !report"
						:view="view"
					/>

					<div v-else-if="!report" class="grid min-h-80 place-items-center text-center">
						<div class="max-w-sm">
							<h2 class="text-lg font-semibold text-highlighted">
								Statistics are unavailable
							</h2>
							<p class="mt-2 text-sm text-muted" role="alert">
								{{ statistics.groupReport.error.value }}
							</p>
							<UButton
								class="mt-5"
								label="Try again"
								color="neutral"
								variant="outline"
								@click="load"
							/>
						</div>
					</div>

					<template v-else>
						<Transition name="statistics-section" mode="out-in">
							<div :key="view" class="space-y-10">
								<template v-if="view === 'activity'">
									<StatisticsActivity :activity="report.activity" />
									<StatisticsDetails :report="report" />
								</template>
								<StatisticsMusicStory
									v-else-if="view === 'music'"
									:report="report"
								/>
								<template v-else-if="view === 'people'">
									<StatisticsListenerLeaderboard
										:listeners="report.top_listeners"
										:total-listening-seconds="report.totals.listening_seconds"
									/>
									<StatisticsGroupHighlights :report="report" />
								</template>
								<StatisticsLibraryStory v-else :library="report.library" />
							</div>
						</Transition>
						<StatisticsCoverageNotice :coverage="report.coverage" />
					</template>
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>

<style scoped>
.statistics-section-enter-active,
.statistics-section-leave-active {
	transition:
		opacity 160ms ease,
		transform 160ms ease;
}
.statistics-section-enter-from {
	opacity: 0;
	transform: translateY(0.35rem);
}
.statistics-section-leave-to {
	opacity: 0;
	transform: translateY(-0.2rem);
}
@media (prefers-reduced-motion: reduce) {
	.statistics-section-enter-active,
	.statistics-section-leave-active {
		transition: none;
	}
}
</style>
