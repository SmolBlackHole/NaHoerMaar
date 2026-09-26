<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
type MetricItem = {
	label: string;
	value: string | number;
	icon: string;
};

defineProps<{
	metrics: readonly MetricItem[];
	secondaryMetrics: readonly MetricItem[];
}>();
</script>

<template>
	<div class="space-y-8" aria-busy="true">
		<StatisticsMetricGrid :items="metrics" loading />
		<USkeleton class="h-3 w-full max-w-md" />

		<section aria-hidden="true">
			<h2 class="text-lg font-semibold text-highlighted">Top listeners</h2>
			<p class="mt-1 text-xs text-muted">Who spent the most time listening in Discord.</p>
			<div class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
				<div
					v-for="index in 4"
					:key="index"
					class="flex items-center gap-3 rounded-xl bg-elevated/40 p-4"
				>
					<USkeleton class="size-10 shrink-0 rounded-full" />
					<div class="min-w-0 flex-1 space-y-2">
						<USkeleton class="h-4 w-24" />
						<USkeleton class="h-3 w-full max-w-28" />
					</div>
					<USkeleton class="size-4 shrink-0" />
				</div>
			</div>
		</section>

		<div class="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(18rem,1fr)]">
			<StatisticsActivity loading />
			<section aria-hidden="true">
				<h2 class="text-lg font-semibold text-highlighted">Where requests came from</h2>
				<p class="mt-1 text-xs text-muted">Manual choices and Radio stay separate.</p>
				<dl class="mt-5 space-y-1 rounded-xl bg-elevated/40 p-2">
					<div
						v-for="label in ['Manual', 'Radio']"
						:key="label"
						class="flex items-center justify-between gap-4 rounded-lg px-3 py-2.5"
					>
						<dt class="text-sm text-muted">{{ label }}</dt>
						<dd><USkeleton class="h-4 w-10" /></dd>
					</div>
				</dl>
			</section>
		</div>

		<div class="grid min-w-0 gap-10 xl:grid-cols-2">
			<section
				v-for="kind in ['tracks', 'artists']"
				:key="kind"
				class="min-w-0"
				aria-hidden="true"
			>
				<h2 class="text-lg font-semibold text-highlighted">
					{{ kind === "tracks" ? "Most played tracks" : "On repeat" }}
				</h2>
				<ol class="mt-4 space-y-2">
					<li
						v-for="index in 5"
						:key="index"
						class="flex min-h-12 items-center gap-3 rounded-lg px-2 py-1.5"
					>
						<USkeleton v-if="kind === 'tracks'" class="size-10 shrink-0 rounded-lg" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-full max-w-64" />
							<USkeleton v-if="kind === 'tracks'" class="h-3 w-28" />
						</div>
						<USkeleton class="h-3 w-14 shrink-0" />
					</li>
				</ol>
			</section>
		</div>

		<StatisticsMetricGrid :items="secondaryMetrics" loading />

		<section aria-hidden="true">
			<div class="flex flex-wrap items-end justify-between gap-3">
				<div>
					<h2 class="text-lg font-semibold text-highlighted">Playback outcomes</h2>
					<p class="mt-1 text-xs text-muted">How resolved plays ended in this period.</p>
				</div>
				<USkeleton class="h-4 w-24" />
			</div>
			<div class="mt-4 rounded-xl bg-elevated/40 p-4 sm:p-5">
				<USkeleton class="h-2 w-full rounded-full" />
				<div class="mt-4 grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-4">
					<div v-for="index in 4" :key="index" class="space-y-2">
						<USkeleton class="h-3 w-16" />
						<USkeleton class="h-6 w-10" />
					</div>
				</div>
				<USkeleton class="mt-4 h-3 w-full max-w-xs" />
			</div>
		</section>

		<section aria-hidden="true">
			<div class="mb-3 flex items-center justify-between gap-4">
				<h2 class="text-lg font-semibold text-highlighted">Recently played</h2>
				<USkeleton class="h-5 w-16" />
			</div>
			<PlayerRecentList :entries="[]" loading />
		</section>
	</div>
</template>
