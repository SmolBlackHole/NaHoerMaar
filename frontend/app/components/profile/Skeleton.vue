<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
withDefaults(defineProps<{ editable?: boolean }>(), { editable: false });
const { icons } = useTheme();
const primaryMetrics = [
	{ label: "Time heard", value: "", icon: icons.value.headphones },
	{ label: "Channel presence", value: "", icon: icons.value.users },
	{ label: "Requests", value: "", icon: icons.value.radio },
	{ label: "Confirmed plays", value: "", icon: icons.value.play },
];
const secondaryMetrics = [
	{ label: "Completed", value: "", icon: icons.value.check },
	{ label: "Skipped", value: "", icon: icons.value.skip },
	{ label: "Different tracks", value: "", icon: icons.value.music },
	{ label: "Different artists", value: "", icon: icons.value.user },
];
</script>

<template>
	<div aria-busy="true">
		<header class="flex flex-wrap items-center justify-between gap-5 pb-2">
			<div class="flex min-w-0 items-center gap-5">
				<USkeleton class="size-20 shrink-0 rounded-full" />
				<div class="min-w-64 space-y-2.5">
					<div class="flex items-center gap-2.5">
						<USkeleton class="h-8 w-48" />
						<USkeleton class="h-5 w-16 rounded-full" />
					</div>
					<USkeleton class="h-4 w-32" />
				</div>
			</div>
			<USkeleton class="h-8 w-40 rounded-md" />
		</header>

		<StatisticsMetricGrid class="mt-7" :items="primaryMetrics" loading />

		<div
			v-if="editable"
			class="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)] lg:items-start"
		>
			<section aria-hidden="true">
				<h2 class="text-lg font-semibold text-highlighted">How friends see you</h2>
				<p class="mt-1 text-sm text-muted">
					Your name and Discord profile picture appear beside requests and radio sessions.
				</p>
				<div class="mt-5 rounded-2xl border border-default bg-elevated/30 p-6 sm:p-8">
					<div class="space-y-6">
						<div class="space-y-2">
							<span class="block text-sm font-medium">What should we call you?</span>
							<USkeleton class="h-11 w-full rounded-md" />
						</div>
						<USkeleton class="h-11 w-full rounded-md" />
					</div>
				</div>
			</section>

			<aside class="space-y-7" aria-hidden="true">
				<section>
					<h2 class="text-lg font-semibold text-highlighted">Discord account</h2>
					<div class="mt-4 flex items-center gap-3">
						<USkeleton class="size-10 shrink-0 rounded-full" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-32" />
							<USkeleton class="h-3 w-44" />
						</div>
					</div>
				</section>
				<section>
					<h2 class="text-lg font-semibold text-highlighted">Account</h2>
					<p class="mt-1 text-sm leading-relaxed text-muted">
						Manage privacy choices or end this browser session.
					</p>
					<div class="mt-4 grid gap-2">
						<USkeleton class="h-8 w-full rounded-md" />
						<USkeleton class="h-8 w-full rounded-md" />
					</div>
					<p class="mt-3 text-xs text-muted">Music keeps playing in Discord.</p>
				</section>
			</aside>
		</div>

		<div class="mt-10 grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(18rem,1fr)]">
			<StatisticsActivity loading />
			<section aria-hidden="true">
				<h2 class="text-lg font-semibold text-highlighted">Requests and outcomes</h2>
				<dl class="mt-4 space-y-1 rounded-xl bg-elevated/40 p-2">
					<div
						v-for="label in ['Manual requests', 'Radio requests', 'Stopped', 'Failed']"
						:key="label"
						class="flex justify-between gap-4 rounded-lg px-3 py-2.5"
					>
						<dt class="text-sm text-muted">{{ label }}</dt>
						<dd><USkeleton class="h-4 w-8" /></dd>
					</div>
				</dl>
			</section>
		</div>

		<StatisticsMetricGrid class="mt-8" :items="secondaryMetrics" loading />

		<div class="mt-10 grid min-w-0 gap-10 xl:grid-cols-3">
			<section
				v-for="section in [
					{ title: 'Most played tracks', details: true, artwork: false },
					{ title: 'Most played artists', details: false, artwork: false },
					{ title: 'Recently heard', details: true, artwork: true },
				]"
				:key="section.title"
				class="min-w-0"
				aria-hidden="true"
			>
				<h2 class="text-lg font-semibold text-highlighted">{{ section.title }}</h2>
				<ol class="mt-5 space-y-2">
					<li
						v-for="index in 5"
						:key="index"
						class="flex min-h-12 items-center gap-3 rounded-lg px-2 py-1.5"
					>
						<USkeleton v-if="section.artwork" class="size-10 shrink-0 rounded-lg" />
						<USkeleton v-else class="h-4 w-5 shrink-0" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-full max-w-52" />
							<USkeleton v-if="section.details" class="h-3 w-24" />
						</div>
						<USkeleton class="h-3 w-14 shrink-0" />
					</li>
				</ol>
			</section>
		</div>
	</div>
</template>
