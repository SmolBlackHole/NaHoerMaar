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
		<nav class="footer-links" aria-label="Project links">
			<a
				href="https://github.com/SmolBlackHole/NaHoerMaar"
				target="_blank"
				rel="noopener noreferrer"
				class="hover:text-highlighted hover:underline"
				>Source</a
			>
			<NuxtLink to="/licenses" class="hover:text-highlighted hover:underline"
				>Licenses</NuxtLink
			>
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
		</nav>
	</footer>
</template>

<style scoped>
.footer-links {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: center;
	gap: 0.5rem 1rem;
}
.is-compact {
	justify-content: flex-start;
	gap: 0.375rem;
	padding: 0.75rem 0.625rem 0;
	font-size: 0.6875rem;
	line-height: 1.5;
}
.is-compact .footer-links,
.is-compact > span {
	flex-basis: 100%;
}
.is-compact .footer-links {
	display: grid;
	grid-template-columns: repeat(2, max-content);
	justify-content: start;
	gap: 0.375rem 1.5rem;
	width: 100%;
}
</style>
