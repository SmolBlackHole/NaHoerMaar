<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
export interface UserDiscoveryItem {
	id: string;
	displayName: string;
	username?: string | null;
	avatarUrl?: string | null;
	detail?: string | null;
}

const props = withDefaults(
	defineProps<{
		items: readonly UserDiscoveryItem[];
		loading?: boolean;
		searchPlaceholder?: string;
		emptyTitle?: string;
		emptyDescription?: string;
	}>(),
	{
		loading: false,
		searchPlaceholder: "Search people",
		emptyTitle: "No matching people",
		emptyDescription: "Try another name.",
	},
);
defineSlots<{
	action(props: { item: UserDiscoveryItem }): unknown;
}>();

const query = defineModel<string>("query", { default: "" });
const { icons } = useTheme();
const visibleItems = computed(() => {
	const needle = query.value.trim().toLocaleLowerCase();
	if (!needle) return props.items;
	return props.items.filter((item) =>
		[item.displayName, item.username, item.detail]
			.filter((value): value is string => Boolean(value))
			.some((value) => value.toLocaleLowerCase().includes(needle)),
	);
});
</script>

<template>
	<div>
		<UInput
			v-model="query"
			:icon="icons.search"
			:placeholder="searchPlaceholder"
			aria-label="Search people"
			class="w-full"
		>
			<template v-if="query" #trailing>
				<UButton
					:icon="icons.close"
					aria-label="Clear search"
					color="neutral"
					variant="link"
					size="xs"
					@click="query = ''"
				/>
			</template>
		</UInput>

		<div class="user-discovery-list mt-3" aria-live="polite">
			<div v-if="loading" class="space-y-2" aria-hidden="true">
				<div v-for="index in 3" :key="index" class="user-discovery-row">
					<USkeleton class="size-9 shrink-0 rounded-full" />
					<div class="min-w-0 flex-1 space-y-2">
						<USkeleton class="h-4 w-28" />
						<USkeleton class="h-3 w-40 max-w-full" />
					</div>
					<USkeleton class="h-8 w-20 rounded-md" />
				</div>
			</div>

			<template v-else-if="visibleItems.length">
				<div v-for="item in visibleItems" :key="item.id" class="user-discovery-row">
					<UAvatar :src="item.avatarUrl ?? undefined" :alt="item.displayName" size="sm" />
					<div class="min-w-0 flex-1">
						<p class="truncate text-sm font-medium text-highlighted">
							{{ item.displayName }}
						</p>
						<p v-if="item.username || item.detail" class="truncate text-xs text-muted">
							<span v-if="item.username">@{{ item.username }}</span>
							<span v-if="item.username && item.detail" aria-hidden="true"> · </span>
							<span v-if="item.detail">{{ item.detail }}</span>
						</p>
					</div>
					<slot name="action" :item="item" />
				</div>
			</template>

			<div v-else class="user-discovery-empty">
				<UIcon :name="icons.users" class="size-5" />
				<p class="text-sm font-medium text-highlighted">{{ emptyTitle }}</p>
				<p class="text-xs text-muted">{{ emptyDescription }}</p>
			</div>
		</div>
	</div>
</template>

<style scoped>
.user-discovery-list {
	display: grid;
	max-height: 18rem;
	overflow-y: auto;
	overscroll-behavior: contain;
}
.user-discovery-row {
	display: flex;
	align-items: center;
	gap: 0.75rem;
	min-width: 0;
	padding: 0.625rem 0.75rem;
	border-radius: 0.75rem;
	transition: background-color 160ms ease;
}
.user-discovery-row:hover {
	background: color-mix(in srgb, var(--ui-bg-elevated) 65%, transparent);
}
.user-discovery-empty {
	display: grid;
	justify-items: center;
	gap: 0.35rem;
	padding: 2rem 1rem;
	text-align: center;
}
@media (prefers-reduced-motion: reduce) {
	.user-discovery-row {
		transition: none;
	}
}
</style>
