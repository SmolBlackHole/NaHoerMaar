<script setup lang="ts">
import type { NuxtError } from "#app";
import { errorPageContent } from "~/utils/errorPage";

const props = defineProps<{ error: NuxtError }>();
const session = useNuxtApp().$backendCore.stores.useSessionStore();
const { icons } = useTheme();
const content = computed(() => errorPageContent(props.error.statusCode));
const bars = [36, 64, 46, 82, 54, 28, 70, 42, 58];

useThemeEffects();
useHead(() => ({ title: `${content.value.code} | NaHörMaar` }));
onMounted(() => void session.restore());

async function returnToPlayer() {
	await clearError({ redirect: "/" });
}

function retry() {
	if (import.meta.client) window.location.reload();
}
</script>

<template>
	<UApp>
		<main class="error-page">
			<header class="error-header">
				<button type="button" class="error-brand" @click="returnToPlayer">
					<UIcon :name="icons.headphones" aria-hidden="true" />
					<span>NaHörMaar</span>
				</button>
				<p class="error-status">Signal {{ content.code }}</p>
			</header>

			<section class="error-stage" aria-labelledby="error-title">
				<div class="error-copy">
					<p class="error-kicker">
						<UIcon :name="icons[content.icon]" aria-hidden="true" />
						Something went off-script
					</p>
					<h1 id="error-title">{{ content.title }}</h1>
					<p class="error-description">{{ content.description }}</p>

					<div class="error-actions">
						<UButton
							label="Back to the player"
							:icon="icons.arrowLeft"
							size="xl"
							@click="returnToPlayer"
						/>
						<UButton
							v-if="content.retryable"
							label="Try again"
							:icon="icons.reload"
							size="xl"
							color="neutral"
							variant="outline"
							@click="retry"
						/>
					</div>
				</div>

				<div class="error-signal" aria-hidden="true">
					<div class="error-code">{{ content.code }}</div>
					<div class="signal-line">
						<span
							v-for="(height, index) in bars"
							:key="index"
							class="signal-bar"
							:style="{ height: `${height}%` }"
						/>
					</div>
					<p>Lost signal</p>
				</div>
			</section>

			<footer class="error-footer">
				<span>© 2026 NaHörMaar</span>
				<nav aria-label="Project links">
					<a
						href="https://github.com/SmolBlackHole/NaHoerMaar"
						target="_blank"
						rel="noopener noreferrer"
						>Source</a
					>
					<NuxtLink to="/licenses">Licenses</NuxtLink>
				</nav>
			</footer>
		</main>
	</UApp>
</template>

<style scoped>
.error-page {
	min-height: 100dvh;
	display: grid;
	grid-template-rows: auto 1fr auto;
	background: var(--room-sidebar);
	color: var(--ui-text);
	overflow: hidden;
}
.error-header,
.error-footer {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 1.5rem;
	padding: 1.5rem clamp(1.25rem, 5vw, 5rem);
}
.error-brand {
	display: inline-flex;
	align-items: center;
	gap: 0.7rem;
	font-size: 1rem;
	font-weight: 650;
	color: var(--ui-text-highlighted);
}
.error-brand .iconify {
	width: 1.55rem;
	height: 1.55rem;
	color: var(--ui-primary);
}
.error-status,
.error-footer {
	font-size: 0.75rem;
	color: var(--ui-text-muted);
}
.error-status {
	font-variant-numeric: tabular-nums;
	letter-spacing: 0.08em;
	text-transform: uppercase;
}
.error-stage {
	display: grid;
	grid-template-columns: minmax(0, 1.05fr) minmax(18rem, 0.95fr);
	align-items: center;
	gap: clamp(3rem, 8vw, 9rem);
	width: min(100%, 92rem);
	margin: 0 auto;
	padding: clamp(3rem, 8vh, 7rem) clamp(1.25rem, 7vw, 7rem);
}
.error-copy {
	position: relative;
	z-index: 1;
	max-width: 48rem;
}
.error-kicker {
	display: inline-flex;
	align-items: center;
	gap: 0.55rem;
	margin-bottom: 1.25rem;
	font-size: 0.78rem;
	font-weight: 600;
	letter-spacing: 0.08em;
	text-transform: uppercase;
	color: var(--ui-primary);
}
.error-kicker .iconify {
	width: 1rem;
	height: 1rem;
}
.error-copy h1 {
	max-width: 18ch;
	font-size: clamp(2.6rem, 6vw, 5.75rem);
	font-weight: 650;
	line-height: 0.98;
	letter-spacing: -0.045em;
	text-wrap: balance;
	color: var(--ui-text-highlighted);
}
.error-description {
	max-width: 42rem;
	margin-top: 1.5rem;
	font-size: clamp(1rem, 1.4vw, 1.2rem);
	line-height: 1.65;
	color: var(--ui-text-muted);
	text-wrap: pretty;
}
.error-actions {
	display: flex;
	flex-wrap: wrap;
	gap: 0.75rem;
	margin-top: 2.25rem;
}
.error-signal {
	position: relative;
	min-height: clamp(20rem, 48vw, 38rem);
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	border: 1px solid color-mix(in srgb, var(--ui-primary) 20%, var(--ui-border));
	border-radius: 2rem;
	background: color-mix(in srgb, var(--ui-primary) 5%, var(--ui-bg));
	box-shadow: 1rem 1rem 0 color-mix(in srgb, var(--ui-primary) 8%, transparent);
	transform: rotate(2deg);
}
.error-code {
	font-size: clamp(7rem, 18vw, 15rem);
	font-weight: 700;
	line-height: 0.78;
	letter-spacing: -0.09em;
	color: color-mix(in srgb, var(--ui-primary) 22%, var(--ui-bg));
}
.signal-line {
	display: flex;
	align-items: center;
	gap: clamp(0.3rem, 0.7vw, 0.65rem);
	width: 58%;
	height: 4.5rem;
	margin-top: 2.5rem;
}
.signal-bar {
	flex: 1;
	min-width: 0.2rem;
	border-radius: 999px;
	background: var(--ui-primary);
	opacity: 0.86;
}
.error-signal p {
	margin-top: 1rem;
	font-size: 0.72rem;
	font-weight: 650;
	letter-spacing: 0.16em;
	text-transform: uppercase;
	color: var(--ui-text-muted);
}
.error-footer nav {
	display: flex;
	gap: 1.25rem;
}
.error-footer a:hover {
	color: var(--ui-text-highlighted);
	text-decoration: underline;
}
@media (max-width: 800px) {
	.error-stage {
		grid-template-columns: 1fr;
		gap: 3rem;
		padding-top: 2.5rem;
	}
	.error-signal {
		min-height: 18rem;
		order: -1;
		transform: rotate(1deg);
	}
	.error-code {
		font-size: clamp(7rem, 34vw, 12rem);
	}
}
@media (max-width: 480px) {
	.error-header,
	.error-footer {
		align-items: flex-start;
	}
	.error-footer {
		flex-direction: column;
	}
	.error-copy h1 {
		font-size: clamp(2.45rem, 13vw, 4rem);
	}
	.error-actions :deep(button) {
		width: 100%;
		justify-content: center;
	}
}
@media (prefers-reduced-motion: reduce) {
	.error-signal {
		transform: none;
	}
}
</style>
