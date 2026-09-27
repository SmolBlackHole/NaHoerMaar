<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<template>
	<div aria-busy="true" aria-label="Loading licenses">
		<USkeleton class="my-6 h-4 w-48" />
		<ul class="mb-6">
			<li
				v-for="index in 10"
				:key="index"
				class="grid grid-cols-1 items-start gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_minmax(12rem,0.7fr)] sm:gap-4"
			>
				<div class="space-y-2">
					<USkeleton class="h-4 w-52 max-w-full" />
					<USkeleton class="h-3 w-20" />
				</div>
				<div class="space-y-2">
					<USkeleton class="h-4 w-24" />
					<USkeleton class="h-3 w-28" />
				</div>
			</li>
		</ul>
	</div>
</template>
