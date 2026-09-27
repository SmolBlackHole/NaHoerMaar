<script setup lang="ts">
const props = defineProps<{
	pageSize: number;
	total: number;
	disabled?: boolean;
}>();
const page = defineModel<number>("page", { required: true });
const emit = defineEmits<{ select: [page: number] }>();
const { icons } = useTheme();
const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)));
const firstItem = computed(() => (props.total ? (page.value - 1) * props.pageSize + 1 : 0));
const lastItem = computed(() => Math.min(page.value * props.pageSize, props.total));
const pageItems = computed(() => {
	const lastPage = pageCount.value;
	if (lastPage <= 7) return Array.from({ length: lastPage }, (_, index) => index + 1);

	const visible = new Set([1, lastPage]);
	for (let candidate = page.value - 2; candidate <= page.value + 2; candidate++) {
		if (candidate > 1 && candidate < lastPage) visible.add(candidate);
	}
	if (page.value <= 4)
		for (let candidate = 2; candidate <= 5; candidate++) visible.add(candidate);
	if (page.value >= lastPage - 3)
		for (let candidate = lastPage - 4; candidate < lastPage; candidate++)
			visible.add(candidate);

	const pages = [...visible].sort((left, right) => left - right);
	const items: (number | "ellipsis")[] = [];
	for (const candidate of pages) {
		const previous = items.at(-1);
		if (typeof previous === "number" && candidate - previous > 1) items.push("ellipsis");
		items.push(candidate);
	}
	return items;
});

function selectPage(nextPage: number) {
	if (props.disabled || nextPage === page.value || nextPage < 1 || nextPage > pageCount.value)
		return;
	page.value = nextPage;
	emit("select", nextPage);
}
</script>

<template>
	<div class="flex min-h-16 items-center justify-between gap-4">
		<div class="min-w-0 text-xs tabular-nums text-muted">
			<p class="hidden sm:block">
				Showing {{ firstItem.toLocaleString() }}-{{ lastItem.toLocaleString() }} of
				{{ total.toLocaleString() }}
			</p>
			<p class="sm:hidden">Page {{ page }} of {{ pageCount }}</p>
		</div>
		<nav class="flex items-center gap-1" aria-label="Pagination">
			<UButton
				:icon="icons.chevronDoubleLeft"
				color="neutral"
				variant="outline"
				size="sm"
				aria-label="First page"
				class="size-8 justify-center p-0"
				:disabled="disabled || page === 1"
				@click="selectPage(1)"
			/>
			<UButton
				:icon="icons.chevronLeft"
				color="neutral"
				variant="outline"
				size="sm"
				aria-label="Previous page"
				class="size-8 justify-center p-0"
				:disabled="disabled || page === 1"
				@click="selectPage(page - 1)"
			/>
			<template v-for="(item, index) in pageItems" :key="`${item}-${index}`">
				<span
					v-if="item === 'ellipsis'"
					class="grid size-8 place-items-center text-sm text-muted"
					aria-hidden="true"
				>
					…
				</span>
				<UButton
					v-else
					:label="String(item)"
					:color="page === item ? 'primary' : 'neutral'"
					:variant="page === item ? 'solid' : 'outline'"
					size="sm"
					square
					class="size-8 justify-center p-0"
					:aria-label="`Page ${item}`"
					:aria-current="page === item ? 'page' : undefined"
					:disabled="disabled"
					@click="selectPage(item)"
				/>
			</template>
			<UButton
				:icon="icons.chevronRight"
				color="neutral"
				variant="outline"
				size="sm"
				aria-label="Next page"
				class="size-8 justify-center p-0"
				:disabled="disabled || page === pageCount"
				@click="selectPage(page + 1)"
			/>
			<UButton
				:icon="icons.chevronDoubleRight"
				color="neutral"
				variant="outline"
				size="sm"
				aria-label="Last page"
				class="size-8 justify-center p-0"
				:disabled="disabled || page === pageCount"
				@click="selectPage(pageCount)"
			/>
		</nav>
	</div>
</template>
