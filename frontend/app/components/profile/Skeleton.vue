<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
withDefaults(defineProps<{ editable?: boolean }>(), { editable: false });
const { icons } = useTheme();
const metrics = [
	{ label: "Time heard", value: "", icon: icons.value.headphones },
	{ label: "Share of group time", value: "", icon: icons.value.users },
	{ label: "Channel presence", value: "", icon: icons.value.clock },
	{ label: "Tracks heard", value: "", icon: icons.value.music },
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

		<StatisticsMetricGrid class="mt-7" :items="metrics" loading />

		<div
			v-if="editable"
			class="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)] lg:items-start"
		>
			<section aria-hidden="true">
				<USkeleton class="h-6 w-44" />
				<USkeleton class="mt-2 h-4 w-full max-w-lg" />
				<div class="mt-5 rounded-2xl border border-default bg-elevated/30 p-6 sm:p-8">
					<div class="space-y-6">
						<div class="space-y-2">
							<USkeleton class="h-4 w-40" />
							<USkeleton class="h-11 w-full rounded-md" />
						</div>
						<USkeleton class="h-11 w-full rounded-md" />
					</div>
				</div>
			</section>

			<aside class="space-y-7" aria-hidden="true">
				<section>
					<USkeleton class="h-6 w-36" />
					<div class="mt-4 flex items-center gap-3">
						<USkeleton class="size-10 shrink-0 rounded-full" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-32" />
							<USkeleton class="h-3 w-44" />
						</div>
					</div>
				</section>
				<section>
					<USkeleton class="h-6 w-24" />
					<USkeleton class="mt-2 h-4 w-full" />
					<div class="mt-4 grid gap-2">
						<USkeleton class="h-8 w-full rounded-md" />
						<USkeleton class="h-8 w-full rounded-md" />
					</div>
				</section>
			</aside>
		</div>

		<section class="mt-10 rounded-2xl bg-elevated/35 p-5" aria-hidden="true">
			<div class="flex items-center justify-between gap-3">
				<USkeleton class="h-4 w-32" />
				<USkeleton class="h-3 w-24" />
			</div>
			<div class="mt-3 flex flex-wrap gap-2">
				<USkeleton
					v-for="width in [28, 24, 32, 20, 30, 26]"
					:key="width"
					class="h-6 rounded-full"
					:style="{ width: `${width * 4}px` }"
				/>
			</div>
		</section>

		<div class="mt-12 grid gap-12 2xl:grid-cols-[minmax(0,1.15fr)_minmax(24rem,0.85fr)]">
			<StatisticsActivity loading scope="personal" />
			<section aria-hidden="true">
				<USkeleton class="h-6 w-40" />
				<USkeleton class="mt-2 h-3 w-56" />
				<div class="mt-5 grid gap-7 xl:grid-cols-[minmax(18rem,0.8fr)_minmax(0,1.2fr)]">
					<div class="space-y-3">
						<div v-for="index in 7" :key="index" class="flex items-center gap-3">
							<USkeleton class="h-3 w-8" />
							<USkeleton class="h-2 flex-1 rounded-full" />
							<USkeleton class="h-3 w-12" />
						</div>
					</div>
					<div class="grid grid-cols-8 gap-1.5 sm:grid-cols-12">
						<USkeleton
							v-for="index in 24"
							:key="index"
							class="aspect-square rounded-md"
						/>
					</div>
				</div>
			</section>
		</div>

		<section class="mt-12" aria-hidden="true">
			<div class="flex justify-between gap-4">
				<div><USkeleton class="h-6 w-36" /><USkeleton class="mt-2 h-3 w-64" /></div>
				<USkeleton class="h-7 w-40 rounded-md" />
			</div>
			<div class="mt-5 grid gap-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(16rem,0.8fr)]">
				<div class="grid gap-2 sm:grid-cols-2">
					<div
						v-for="index in 6"
						:key="index"
						class="flex items-center gap-3 rounded-xl p-2.5"
					>
						<SharedTrackIdentity loading :position="index" class="min-w-0 flex-1" />
						<USkeleton class="h-3 w-14" />
					</div>
				</div>
				<div class="space-y-4">
					<USkeleton v-for="index in 5" :key="index" class="h-5 w-full" />
				</div>
			</div>
		</section>

		<section class="mt-12" aria-hidden="true">
			<USkeleton class="h-6 w-36" />
			<USkeleton class="mt-2 h-3 w-80" />
			<div class="mt-5 grid gap-3 xl:grid-cols-3">
				<div class="h-52 rounded-2xl bg-elevated/40 p-5">
					<USkeleton class="h-4 w-32" />
					<div class="mt-6 space-y-4">
						<USkeleton class="h-10 w-full" /><USkeleton class="h-10 w-full" /><USkeleton
							class="h-3 w-40"
						/>
					</div>
				</div>
				<div v-for="card in 2" :key="card" class="h-52 rounded-2xl bg-elevated/40 p-5">
					<USkeleton class="h-4 w-32" />
					<div class="mt-4 space-y-3">
						<div v-for="row in 3" :key="row" class="flex items-center gap-3">
							<SharedTrackIdentity loading size="sm" class="min-w-0 flex-1" />
							<USkeleton class="h-3 w-12" />
						</div>
					</div>
				</div>
			</div>
		</section>

		<section class="mt-12" aria-hidden="true">
			<USkeleton class="h-6 w-32" />
			<USkeleton class="mt-2 h-3 w-64" />
			<div class="mt-5 grid gap-2 lg:grid-cols-2">
				<div
					v-for="index in 8"
					:key="index"
					class="flex items-center gap-3 rounded-xl p-2.5"
				>
					<SharedTrackIdentity loading class="min-w-0 flex-1" />
					<div class="space-y-2">
						<USkeleton class="h-3 w-12" /><USkeleton class="h-3 w-20" />
					</div>
				</div>
			</div>
		</section>
	</div>
</template>
