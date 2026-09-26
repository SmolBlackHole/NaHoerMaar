<script setup lang="ts">
import type { StatisticsPeriod } from "~/core/models/account";

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Overview | NaHörMaar" });
const core = useNuxtApp().$backendCore;
const player = core.stores.usePlayerStore();
const statistics = core.workflows.statistics();
const recent = core.workflows.recent();
const { icons } = useTheme();
const period = ref<StatisticsPeriod>("7d");
const report = computed(() => statistics.report.data.value);
const recentTracks = computed(() => (recent.recent.data.value ?? []).slice(0, 5));
const maxPlays = computed(() =>
	Math.max(1, ...(report.value?.daily_activity.map(({ plays }) => plays) ?? [1])),
);
const periodItems = [
	{ label: "Last 7 days", value: "7d" },
	{ label: "Last 30 days", value: "30d" },
	{ label: "All time", value: "all" },
];
const metrics = computed(() => {
	const totals = report.value?.totals;
	return [
		{ label: "Tracks played", value: totals?.plays ?? 0, icon: icons.value.play },
		{
			label: "Listening time",
			value: duration(totals?.listening_seconds ?? 0),
			icon: icons.value.headphones,
		},
		{ label: "Requests", value: totals?.requests ?? 0, icon: icons.value.list },
		{ label: "In queue", value: player.state?.queue.length ?? 0, icon: icons.value.music },
	];
});

function duration(seconds: number) {
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	return `${hours}h ${minutes % 60}m`;
}
function dayLabel(value: string) {
	return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(
		new Date(value),
	);
}
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
	<UDashboardPanel id="overview" class="min-h-0 min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
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
					<USelect
						v-model="period"
						:items="periodItems"
						value-key="value"
						aria-label="Activity period"
						class="w-40"
					/>
				</div>

				<div
					v-if="statistics.report.loading.value && !report"
					class="space-y-7"
					aria-busy="true"
				>
					<div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
						<div
							v-for="index in 4"
							:key="index"
							class="space-y-3 rounded-xl border border-default p-5"
						>
							<USkeleton class="h-4 w-28" />
							<USkeleton class="h-8 w-20" />
						</div>
					</div>
					<div class="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(16rem,1fr)]">
						<USkeleton class="h-80 rounded-xl" />
						<USkeleton class="h-80 rounded-xl" />
					</div>
				</div>

				<div v-else-if="!report" class="grid min-h-80 place-items-center text-center">
					<div class="max-w-sm">
						<UIcon :name="icons.warning" class="mx-auto size-10 text-warning" />
						<h2 class="mt-4 text-lg font-semibold text-highlighted">
							Statistics are unavailable
						</h2>
						<p class="mt-2 text-sm text-muted" role="alert">
							{{ statistics.report.error.value }}
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
					<dl class="grid grid-cols-2 gap-4 xl:grid-cols-4">
						<div
							v-for="metric in metrics"
							:key="metric.label"
							class="rounded-xl border border-default p-5"
						>
							<dt class="flex items-center gap-2 text-sm text-muted">
								<UIcon :name="metric.icon" class="size-4" />{{ metric.label }}
							</dt>
							<dd class="mt-4 text-3xl font-semibold tabular-nums text-highlighted">
								{{ metric.value }}
							</dd>
						</div>
					</dl>

					<div class="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(16rem,1fr)]">
						<section class="min-w-0" aria-labelledby="activity-heading">
							<h2
								id="activity-heading"
								class="text-lg font-semibold text-highlighted"
							>
								Daily activity
							</h2>
							<p class="mt-1 text-xs text-muted">
								Playback starts recorded by the new backend.
							</p>
							<figure class="mt-8">
								<div
									class="flex h-44 items-end gap-1 border-b border-default sm:gap-2"
								>
									<UTooltip
										v-for="day in report.daily_activity"
										:key="day.day"
										:text="`${dayLabel(day.day)}: ${day.plays} plays, ${duration(day.listening_seconds)} listened`"
									>
										<div class="flex h-full min-w-0 flex-1 items-end">
											<div
												class="w-full rounded-t-sm bg-primary/75"
												:style="{
													height: `${Math.max(day.plays ? 5 : 0, (day.plays / maxPlays) * 100)}%`,
												}"
											/>
										</div>
									</UTooltip>
								</div>
								<figcaption
									v-if="report.daily_activity.length"
									class="mt-3 flex justify-between text-xs text-muted"
								>
									<span>{{ dayLabel(report.daily_activity[0]!.day) }}</span>
									<span>{{ dayLabel(report.daily_activity.at(-1)!.day) }}</span>
								</figcaption>
							</figure>
						</section>

						<section aria-labelledby="artists-heading">
							<h2 id="artists-heading" class="text-lg font-semibold text-highlighted">
								On repeat
							</h2>
							<p class="mt-1 text-xs text-muted">Artists ranked by plays.</p>
							<ol v-if="report.top_artists.length" class="mt-5 space-y-5">
								<li
									v-for="artist in report.top_artists.slice(0, 6)"
									:key="artist.artist_id"
									class="space-y-2"
								>
									<div class="flex justify-between gap-3 text-sm">
										<span class="truncate">{{ artist.name }}</span>
										<span class="text-muted tabular-nums">{{
											artist.plays
										}}</span>
									</div>
									<div class="h-1 rounded-full bg-elevated">
										<div
											class="h-full rounded-full bg-primary/60"
											:style="{
												width: `${(artist.plays / report.top_artists[0]!.plays) * 100}%`,
											}"
										/>
									</div>
								</li>
							</ol>
							<p v-else class="mt-8 text-sm text-muted">
								Play a few tracks to see who is on repeat.
							</p>
						</section>
					</div>

					<section
						class="border-t border-default pt-6"
						aria-labelledby="listeners-heading"
					>
						<div class="mb-4 flex items-end justify-between gap-4">
							<div>
								<h2
									id="listeners-heading"
									class="text-lg font-semibold text-highlighted"
								>
									Top listeners
								</h2>
								<p class="mt-1 text-xs text-muted">
									Time spent listening in Discord.
								</p>
							</div>
						</div>
						<div
							v-if="report.top_listeners.length"
							class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
						>
							<NuxtLink
								v-for="listener in report.top_listeners.slice(0, 8)"
								:key="listener.user_id"
								:to="`/profile/${listener.user_id}`"
								class="flex items-center gap-3 rounded-xl border border-default p-4 hover:bg-elevated/50"
							>
								<img
									v-if="listener.pixabot"
									:src="`/avatars/${listener.pixabot}.png`"
									alt=""
									class="size-10 rounded-lg [image-rendering:pixelated]"
								/>
								<UAvatar
									v-else
									:src="listener.discord_avatar_url ?? undefined"
									:alt="
										listener.discord_display_name ??
										listener.discord_username ??
										'Listener'
									"
									size="lg"
								/>
								<div class="min-w-0">
									<p class="truncate text-sm font-medium text-highlighted">
										{{
											listener.display_name ??
											listener.discord_display_name ??
											listener.discord_username ??
											"Listener"
										}}
									</p>
									<p class="mt-1 text-xs text-muted">
										{{ duration(listener.listening_seconds) }}
									</p>
								</div>
							</NuxtLink>
						</div>
						<p v-else class="text-sm text-muted">
							Listening time appears once people join the channel.
						</p>
					</section>

					<section class="border-t border-default pt-6">
						<div class="mb-3 flex items-center justify-between gap-4">
							<h2 class="text-lg font-semibold text-highlighted">Recently played</h2>
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
