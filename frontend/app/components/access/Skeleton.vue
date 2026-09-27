<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
const metrics = ["Your role", "Allowed listeners", "Available members", "Operators"];
</script>

<template>
	<div aria-busy="true" aria-label="Loading listener access">
		<dl
			class="grid overflow-hidden rounded-2xl border border-default sm:grid-cols-2 xl:grid-cols-4"
		>
			<div v-for="label in metrics" :key="label" class="space-y-3 p-5">
				<dt class="flex items-center gap-2 text-sm text-muted">
					<USkeleton class="size-4 shrink-0 rounded" />
					{{ label }}
				</dt>
				<dd><USkeleton class="h-8 w-20" /></dd>
			</div>
		</dl>

		<section class="mt-8" aria-hidden="true">
			<h2 class="text-lg font-semibold text-highlighted">Operators</h2>
			<p class="mt-1 text-xs text-muted">Owner and admin roles are managed on the host.</p>
			<div class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
				<div
					v-for="index in 4"
					:key="index"
					class="flex min-h-16 items-center gap-3 rounded-xl bg-elevated/35 px-3 py-2.5"
				>
					<USkeleton class="size-8 shrink-0 rounded-full" />
					<div class="min-w-0 flex-1 space-y-2">
						<USkeleton class="h-4 w-24" />
						<USkeleton class="h-3 w-16" />
					</div>
					<USkeleton class="h-5 w-14 rounded-full" />
					<USkeleton class="size-4" />
				</div>
			</div>
		</section>

		<div class="mt-8 grid gap-8 xl:grid-cols-2">
			<section
				v-for="section in [
					{
						title: 'Available on Discord',
						description: 'People from every connected server',
					},
					{
						title: 'Allowed listeners',
						description: 'People who can use the player now',
					},
				]"
				:key="section.title"
				aria-hidden="true"
			>
				<div class="flex items-start justify-between gap-4">
					<div>
						<h2 class="text-lg font-semibold text-highlighted">{{ section.title }}</h2>
						<p class="mt-1 text-xs text-muted">{{ section.description }}</p>
					</div>
					<USkeleton class="h-3 w-16" />
				</div>
				<USkeleton class="mt-5 h-8 w-full rounded-md" />
				<div class="mt-3 space-y-1">
					<div
						v-for="index in 5"
						:key="index"
						class="flex min-h-16 items-center gap-3 rounded-xl px-3 py-2.5"
					>
						<USkeleton class="size-8 shrink-0 rounded-full" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-36" />
							<USkeleton class="h-3 w-52 max-w-full" />
						</div>
						<USkeleton class="h-8 w-16 rounded-md" />
					</div>
				</div>
			</section>
		</div>
	</div>
</template>
