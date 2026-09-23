<script setup lang="ts">
defineProps<{ compact?: boolean }>();
const consent = useConsentStore();
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
		<NuxtLink to="/licenses" class="hover:text-highlighted hover:underline">Licenses</NuxtLink>
		<UTooltip text="Avatars by Pixabots">
			<a
				href="https://pixabots.com"
				target="_blank"
				rel="noopener noreferrer"
				class="hover:text-highlighted hover:underline"
				>{{ compact ? "Pixabots" : "Avatars by Pixabots" }}</a
			>
		</UTooltip>
		<button
			type="button"
			class="hover:text-highlighted hover:underline"
			@click="consent.open = true"
		>
			Cookies
		</button>
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
