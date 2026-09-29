<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
type MetricItem = {
	label: string;
	value: string | number;
	icon: string;
};

defineProps<{ metrics: readonly MetricItem[] }>();
</script>

<template>
	<div class="space-y-10" aria-busy="true">
		<StatisticsMetricGrid :items="metrics" loading />

		<section aria-hidden="true">
			<div class="flex flex-wrap items-end justify-between gap-3">
				<div>
					<h2 class="text-lg font-semibold text-highlighted">Top listeners</h2>
					<p class="mt-1 text-xs text-muted">
						Who spent the most time listening in Discord.
					</p>
				</div>
				<USkeleton class="h-3 w-20" />
			</div>
			<div class="mt-4 grid gap-3 xl:grid-cols-[minmax(18rem,0.9fr)_minmax(0,1.1fr)]">
				<div class="rounded-2xl border border-default bg-elevated/30 p-5">
					<div class="flex items-start gap-4">
						<USkeleton class="size-16 shrink-0 rounded-full" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-3 w-20" />
							<USkeleton class="h-6 w-32" />
							<USkeleton class="h-4 w-24" />
						</div>
						<USkeleton class="size-5 shrink-0" />
					</div>
					<div class="mt-5 flex items-center justify-between">
						<USkeleton class="h-3 w-28" />
						<USkeleton class="h-3 w-8" />
					</div>
					<USkeleton class="mt-2 h-1.5 w-full rounded-full" />
					<div class="mt-4 flex flex-wrap gap-1.5">
						<USkeleton class="h-5 w-24 rounded-full" />
						<USkeleton class="h-5 w-28 rounded-full" />
						<USkeleton class="h-5 w-20 rounded-full" />
					</div>
				</div>
				<div class="grid gap-2">
					<div
						v-for="index in 3"
						:key="index"
						class="flex items-center gap-3 rounded-xl bg-elevated/35 px-3 py-2.5"
					>
						<USkeleton class="h-3 w-5 shrink-0" />
						<USkeleton class="size-8 shrink-0 rounded-full" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-24" />
							<USkeleton class="h-3 w-20" />
						</div>
						<div class="hidden items-center gap-1.5 lg:flex">
							<USkeleton class="h-5 w-20 rounded-full" />
							<USkeleton class="h-5 w-24 rounded-full" />
							<USkeleton class="h-5 w-16 rounded-full" />
							<USkeleton class="h-3 w-5" />
						</div>
						<USkeleton class="size-4 shrink-0" />
					</div>
				</div>
			</div>
		</section>

		<StatisticsActivity loading />

		<section aria-hidden="true">
			<h2 class="text-lg font-semibold text-highlighted">The music</h2>
			<p class="mt-1 text-xs text-muted">The tracks and artists that kept coming back.</p>
			<div class="mt-4 grid gap-8 xl:grid-cols-[minmax(17rem,30rem)_minmax(0,1fr)]">
				<div
					class="flex aspect-4/3 min-h-56 min-w-0 w-full flex-col rounded-2xl bg-elevated/45 p-5 sm:aspect-video"
				>
					<div class="flex items-center justify-between gap-3">
						<USkeleton class="h-7 w-24 rounded-full" />
						<USkeleton class="h-7 w-16 rounded-full" />
					</div>
					<div class="mt-auto space-y-3">
						<USkeleton class="h-9 w-full max-w-72" />
						<USkeleton class="h-4 w-32" />
						<USkeleton class="h-3 w-36" />
					</div>
				</div>

				<div class="grid min-w-0 gap-8 md:grid-cols-2">
					<section class="min-w-0">
						<h3 class="text-sm font-semibold text-highlighted">More on repeat</h3>
						<ol class="mt-3 space-y-1">
							<li
								v-for="index in 4"
								:key="index"
								class="flex items-center gap-3 rounded-lg px-2 py-1.5"
							>
								<SharedTrackIdentity loading size="sm" class="min-w-0 flex-1" />
								<USkeleton class="h-3 w-6 shrink-0" />
							</li>
						</ol>
					</section>

					<section class="min-w-0">
						<h3 class="text-sm font-semibold text-highlighted">Artists on repeat</h3>
						<ol class="mt-3 space-y-1">
							<li
								v-for="index in 5"
								:key="index"
								class="flex items-center gap-3 rounded-lg px-2 py-2"
							>
								<USkeleton class="h-3 w-4 shrink-0" />
								<USkeleton class="h-4 min-w-0 flex-1" />
								<USkeleton class="h-3 w-6 shrink-0" />
							</li>
						</ol>
					</section>
				</div>
			</div>
		</section>

		<section aria-hidden="true">
			<h2 class="text-lg font-semibold text-highlighted">Group highlights</h2>
			<p class="mt-1 text-xs text-muted">The small stories hiding inside this period.</p>
			<div class="mt-4 grid gap-3 sm:grid-cols-2">
				<div v-for="index in 4" :key="index" class="min-w-0 rounded-2xl bg-elevated/40 p-5">
					<div class="flex items-center gap-3">
						<USkeleton class="size-9 rounded-xl" />
						<USkeleton class="h-3 w-24" />
					</div>
					<USkeleton class="mt-3 h-6 w-full max-w-52" />
					<USkeleton class="mt-2 h-4 w-full max-w-72" />
					<USkeleton class="mt-1 h-4 w-2/3" />
				</div>
			</div>
		</section>

		<div class="flex items-center justify-between gap-4 rounded-2xl bg-elevated/35 px-5 py-4">
			<div class="space-y-2">
				<div class="text-sm font-semibold text-highlighted">
					Requests, outcomes and coverage
				</div>
				<div class="text-xs text-muted">The useful detail behind the headline numbers.</div>
			</div>
			<USkeleton class="size-5 shrink-0" />
		</div>

		<section aria-hidden="true">
			<div class="mb-3 flex items-center justify-between gap-4">
				<div>
					<h2 class="text-lg font-semibold text-highlighted">Recently played</h2>
					<p class="mt-1 text-xs text-muted">The latest tracks heard by the group.</p>
				</div>
				<USkeleton class="h-5 w-16" />
			</div>
			<PlayerRecentList :entries="[]" loading />
		</section>
	</div>
</template>
