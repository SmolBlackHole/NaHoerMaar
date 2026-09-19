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
		class="py-2 font-semibold"
		:ui="{ leadingIcon: 'text-primary' }"
		@click="emit('navigate')"
	/>
</template>
