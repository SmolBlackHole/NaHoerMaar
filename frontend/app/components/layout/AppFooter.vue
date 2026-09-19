<script setup lang="ts">
defineProps<{ compact?: boolean }>();
</script>

<template>
	<footer
		:class="{ 'is-compact': compact }"
		class="text-muted flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-xs"
	>
		<span>© 2026 NaHörMaar</span>
		<a
			href="https://github.com/SmolBlackHole/NaHoerMaar"
			target="_blank"
			rel="noopener noreferrer"
			class="hover:text-highlighted hover:underline"
			>Source</a
		>
		<a
			href="https://github.com/SmolBlackHole/NaHoerMaar/blob/main/LICENSE"
			target="_blank"
			rel="noopener noreferrer"
			class="hover:text-highlighted hover:underline"
			>License</a
		>
		<a
			href="https://pixabots.com"
			title="Avatars by Pixabots"
			target="_blank"
			rel="noopener noreferrer"
			class="hover:text-highlighted hover:underline"
			>{{ compact ? "Pixabots" : "Avatars by Pixabots" }}</a
		>
	</footer>
</template>

<style scoped>
.is-compact {
	justify-content: flex-start;
	gap: 0.375rem 0.75rem;
	padding: 0.75rem 0.625rem 0;
	font-size: 0.6875rem;
	line-height: 1.5;
}
.is-compact > span {
	flex-basis: 100%;
}
</style>
