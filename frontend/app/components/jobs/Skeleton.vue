<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<template>
	<div aria-busy="true" aria-label="Loading background jobs" class="space-y-10">
		<div
			class="grid overflow-hidden rounded-2xl border border-default sm:grid-cols-2 xl:grid-cols-4"
		>
			<div
				v-for="metric in 4"
				:key="metric"
				class="space-y-3 border-t border-default p-5 first:border-t-0 sm:border-r sm:first:border-t-0 xl:border-t-0"
			>
				<USkeleton class="h-4 w-28" />
				<USkeleton class="h-7 w-20" />
			</div>
		</div>

		<section>
			<USkeleton class="h-6 w-32" />
			<USkeleton class="mt-2 h-3 w-80 max-w-full" />
			<div class="mt-4 grid gap-4 xl:grid-cols-2">
				<div v-for="card in 2" :key="card" class="rounded-2xl bg-elevated/35 p-5 sm:p-6">
					<div class="flex gap-4">
						<USkeleton class="size-11 shrink-0 rounded-xl" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-5 w-44" />
							<USkeleton class="h-4 w-full max-w-sm" />
						</div>
					</div>
					<div class="mt-6 grid grid-cols-3 gap-4">
						<div v-for="fact in 3" :key="fact" class="space-y-2">
							<USkeleton class="h-3 w-16" />
							<USkeleton class="h-4 w-24 max-w-full" />
						</div>
					</div>
					<div class="mt-6 flex items-end justify-between gap-4">
						<USkeleton class="h-4 w-36" />
						<div class="flex items-end gap-2">
							<USkeleton class="h-8 w-24 rounded-md" />
							<USkeleton class="h-8 w-24 rounded-md" />
						</div>
					</div>
				</div>
			</div>
		</section>

		<section>
			<USkeleton class="h-6 w-28" />
			<USkeleton class="mt-2 h-3 w-96 max-w-full" />
			<div class="mt-4 grid gap-2">
				<div
					v-for="row in 3"
					:key="row"
					class="grid gap-3 rounded-xl bg-elevated/35 p-4 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] sm:items-center"
				>
					<div class="flex items-center gap-3">
						<USkeleton class="size-9 shrink-0 rounded-full" />
						<div class="space-y-2">
							<USkeleton class="h-4 w-40" />
							<USkeleton class="h-3 w-52" />
						</div>
					</div>
					<USkeleton class="h-4 w-20" />
					<USkeleton class="h-4 w-12" />
					<USkeleton class="h-5 w-16 rounded-full" />
					<USkeleton class="size-4" />
				</div>
			</div>
		</section>
	</div>
</template>
