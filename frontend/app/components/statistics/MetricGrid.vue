<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
withDefaults(
	defineProps<{
		items: readonly {
			label: string;
			value: string | number;
			icon: string;
		}[];
		loading?: boolean;
	}>(),
	{ loading: false },
);
</script>

<template>
	<dl
		class="grid overflow-hidden rounded-2xl border border-default bg-elevated/20 sm:grid-cols-2 xl:grid-cols-4"
	>
		<div
			v-for="(item, index) in items"
			:key="item.label"
			class="p-5"
			:class="[
				index > 0 && 'border-t border-default sm:border-t-0',
				index % 2 === 1 && 'sm:border-l sm:border-default',
				index === 2 && 'sm:border-l-0 sm:border-t xl:border-l xl:border-t-0',
				index === 3 && 'sm:border-t xl:border-t-0',
			]"
		>
			<dt class="flex items-center gap-3 text-sm text-muted">
				<span
					class="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"
				>
					<UIcon :name="item.icon" class="size-4" />
				</span>
				{{ item.label }}
			</dt>
			<dd class="mt-4 text-2xl font-semibold tabular-nums text-highlighted">
				<USkeleton v-if="loading" class="h-8 w-20" />
				<template v-else>{{ item.value }}</template>
			</dd>
		</div>
	</dl>
</template>
