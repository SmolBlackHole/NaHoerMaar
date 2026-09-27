<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<template>
	<div aria-busy="true" aria-label="Loading bot logs">
		<div
			v-for="index in 10"
			:key="index"
			class="grid gap-x-3 px-4 py-2 sm:grid-cols-[10rem_5rem_6rem_10rem_12rem_minmax(0,1fr)] sm:py-1.5"
		>
			<USkeleton class="h-3 w-32" />
			<USkeleton class="h-3 w-12" />
			<USkeleton class="h-3 w-14" />
			<USkeleton class="h-3 w-24" />
			<USkeleton class="h-3 w-36" />
			<USkeleton class="h-3 w-full" />
		</div>
	</div>
</template>
