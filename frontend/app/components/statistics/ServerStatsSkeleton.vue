<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ActivityBucket } from "~/core/models/statistics";

defineProps<{
	view: "activity" | "music" | "people" | "library";
	granularity?: ActivityBucket["granularity"];
}>();
</script>

<template>
	<div class="space-y-10" aria-busy="true">
		<template v-if="view === 'activity'">
			<StatisticsActivity loading :granularity="granularity" />
			<div class="rounded-2xl bg-elevated/35 p-5">
				<div class="flex items-center justify-between gap-4">
					<div class="space-y-2">
						<USkeleton class="h-4 w-52" />
						<USkeleton class="h-3 w-72 max-w-full" />
					</div>
					<USkeleton class="size-5 shrink-0" />
				</div>
			</div>
		</template>

		<section v-else-if="view === 'music'" aria-hidden="true">
			<h2 class="text-lg font-semibold text-highlighted">The music</h2>
			<p class="mt-1 text-xs text-muted">The tracks and artists that kept coming back.</p>
			<div class="mt-4 grid gap-8 xl:grid-cols-[minmax(17rem,30rem)_minmax(0,1fr)]">
				<div
					class="flex aspect-4/3 min-h-56 flex-col rounded-2xl bg-elevated/45 p-5 sm:aspect-video"
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
					<section v-for="column in 2" :key="column" class="min-w-0">
						<USkeleton class="h-4 w-32" />
						<div class="mt-3 space-y-1">
							<div
								v-for="index in 5"
								:key="index"
								class="flex items-center gap-3 px-2 py-2"
							>
								<USkeleton
									v-if="column === 1"
									class="size-10 shrink-0 rounded-md"
								/>
								<USkeleton class="h-4 min-w-0 flex-1" />
								<USkeleton class="h-3 w-6 shrink-0" />
							</div>
						</div>
					</section>
				</div>
			</div>
		</section>

		<template v-else-if="view === 'people'">
			<section aria-hidden="true">
				<div class="flex items-end justify-between gap-4">
					<div class="space-y-2">
						<USkeleton class="h-5 w-28" />
						<USkeleton class="h-3 w-64 max-w-full" />
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
						</div>
						<USkeleton class="mt-5 h-1.5 w-full rounded-full" />
					</div>
					<div class="grid gap-2">
						<div
							v-for="index in 7"
							:key="index"
							class="flex items-center gap-3 rounded-xl bg-elevated/35 px-3 py-2.5"
						>
							<USkeleton class="h-3 w-5 shrink-0" />
							<USkeleton class="size-8 shrink-0 rounded-full" />
							<div class="min-w-0 flex-1 space-y-2">
								<USkeleton class="h-4 w-24" />
								<USkeleton class="h-3 w-20" />
							</div>
						</div>
					</div>
				</div>
			</section>
			<section aria-hidden="true">
				<USkeleton class="h-5 w-36" />
				<USkeleton class="mt-2 h-3 w-64 max-w-full" />
				<div class="mt-4 grid gap-3 sm:grid-cols-2">
					<div v-for="index in 4" :key="index" class="rounded-2xl bg-elevated/40 p-5">
						<USkeleton class="h-4 w-28" />
						<USkeleton class="mt-4 h-6 w-48 max-w-full" />
						<USkeleton class="mt-2 h-4 w-full" />
					</div>
				</div>
			</section>
		</template>

		<StatisticsLibraryStory v-else :loading="true" />
	</div>
</template>
