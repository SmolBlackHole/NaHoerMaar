<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<template>
	<UCard aria-busy="true" aria-label="Loading background jobs">
		<template #header>
			<div class="flex flex-wrap items-start justify-between gap-3">
				<div class="min-w-0 flex-1 space-y-2">
					<div class="flex items-center gap-2">
						<USkeleton class="h-5 w-44" />
						<USkeleton class="h-5 w-12 rounded-full" />
					</div>
					<USkeleton class="h-4 w-full max-w-xl" />
				</div>
				<div class="flex items-end gap-2">
					<div class="space-y-1">
						<span class="text-xs text-muted">Entries</span>
						<USkeleton class="h-8 w-24 rounded-md" />
					</div>
					<USkeleton class="h-8 w-24 rounded-md" />
				</div>
			</div>
		</template>

		<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
			<div v-for="index in 4" :key="index" class="rounded-lg bg-elevated/50 p-4">
				<USkeleton class="h-3 w-24" />
				<USkeleton class="mt-2 h-5 w-36 max-w-full" />
				<USkeleton class="mt-2 h-3 w-28 max-w-full" />
			</div>
		</div>
	</UCard>
</template>
