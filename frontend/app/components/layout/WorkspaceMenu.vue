<script setup lang="ts">
defineProps<{ collapsed?: boolean }>();
const emit = defineEmits<{ navigate: [] }>();
const { icons } = useTheme();
</script>

<template>
	<UButton
		to="/"
		:label="collapsed ? undefined : 'NaHörMaar'"
		:icon="icons.headphones"
		color="neutral"
		variant="ghost"
		block
		:square="collapsed"
		aria-label="NaHörMaar player"
		class="min-w-0 gap-3 py-2 text-base font-semibold tracking-tight"
		:class="collapsed ? 'justify-center px-0' : 'justify-start px-2.5'"
		:ui="{ leadingIcon: 'text-primary' }"
		@click="emit('navigate')"
	/>
</template>
