<script setup lang="ts">
import type { LicenseInventory } from "#shared/licenses";

definePageMeta({ layout: false });
useSeoMeta({ title: "Licenses | NaHörMaar" });
const { icons } = useTheme();
const query = ref("");
const category = ref("All components");
const { data, status, error, refresh } = await useFetch<LicenseInventory>(
	"/licenses/generated/index.json",
	{ server: false },
);
const categories = [
	"All components",
	"Project",
	"JavaScript",
	"Python",
	"Audio",
	"Fonts & artwork",
];
const filtered = computed(() => {
	const term = query.value.trim().toLowerCase();
	return (data.value?.packages ?? []).filter(
		(item) =>
			(category.value === "All components" || item.category === category.value) &&
			`${item.name} ${item.license}`.toLowerCase().includes(term),
	);
});
const visibleCount = ref(40);
watch([query, category], () => {
	visibleCount.value = 40;
});
const visible = computed(() => filtered.value.slice(0, visibleCount.value));
</script>

<template>
	<main class="licenses-page">
		<div class="licenses-content">
			<NuxtLink to="/" class="back-link"
				><UIcon :name="icons.arrowLeft" />Back to player</NuxtLink
			>
			<header>
				<h1>Licenses</h1>
				<p>
					NaHörMaar is licensed under the MPL 2.0. These are the projects, fonts and
					artwork it uses, with their own licenses.
				</p>
				<div class="license-downloads">
					<a
						href="https://github.com/SmolBlackHole/NaHoerMaar/blob/main/LICENSE"
						target="_blank"
						rel="noopener noreferrer"
						>Project license<UIcon :name="icons.external"
					/></a>
					<a v-if="data" href="/licenses/generated/notices.txt" download
						>Download all notices<UIcon :name="icons.arrowDown"
					/></a>
				</div>
			</header>
			<div class="license-filters">
				<UInput
					v-model="query"
					:icon="icons.search"
					placeholder="Find a component or license"
					aria-label="Find a component or license"
					size="lg"
					class="min-w-0 flex-1"
				/>
				<USelect
					v-model="category"
					:items="categories"
					aria-label="Component type"
					size="lg"
					class="category-select"
				/>
			</div>
			<p v-if="error" role="alert" class="license-status">
				The license list could not be loaded.
				<button type="button" @click="refresh()">Try again</button>
			</p>
			<p
				v-else-if="status === 'pending' || status === 'idle'"
				role="status"
				class="license-status"
			>
				Loading licenses…
			</p>
			<template v-else>
				<p class="license-count" role="status">
					{{ filtered.length }} components, including build tools
				</p>
				<ul v-if="visible.length" class="license-list">
					<li
						v-for="item in visible"
						:key="`${item.category}:${item.name}@${item.version}`"
					>
						<div class="component-name">
							<a
								v-if="item.url"
								:href="item.url"
								target="_blank"
								rel="noopener noreferrer"
								>{{ item.name }}<UIcon :name="icons.external"
							/></a>
							<span v-else>{{ item.name }}</span>
							<span class="component-version">{{ item.version }}</span>
						</div>
						<div class="component-license">
							<span>{{ item.license }}</span>
							<a
								v-if="item.textUrl"
								:href="item.textUrl"
								target="_blank"
								rel="noopener noreferrer"
								:aria-label="`Read license for ${item.name}`"
								>Read license<UIcon :name="icons.external"
							/></a>
							<span v-else class="missing-notice">No license text included</span>
						</div>
					</li>
				</ul>
				<p v-else class="license-status">No components match this search.</p>
				<UButton
					v-if="visibleCount < filtered.length"
					color="neutral"
					variant="soft"
					@click="visibleCount += 40"
					>Show more components</UButton
				>
			</template>
			<LayoutAppFooter />
		</div>
	</main>
</template>

<style scoped>
.licenses-page {
	height: 100dvh;
	overflow-y: auto;
	background: var(--ui-bg);
}
.licenses-content {
	max-width: 64rem;
	margin-inline: auto;
	padding: 2rem 2rem 3rem;
}
.back-link,
.license-downloads a,
.component-name a,
.component-license a {
	display: inline-flex;
	align-items: center;
	gap: 0.5rem;
}
a:hover,
button:hover {
	color: var(--ui-text-highlighted);
	text-decoration: underline;
	text-underline-offset: 0.2em;
}
.back-link {
	min-height: 2.75rem;
	color: var(--ui-text-muted);
	font-size: 0.875rem;
}
header {
	margin-top: 2.5rem;
}
h1 {
	font-size: clamp(2rem, 5vw, 3rem);
	font-weight: 600;
	letter-spacing: -0.03em;
	color: var(--ui-text-highlighted);
}
header p {
	max-width: 65ch;
	margin-top: 1rem;
	color: var(--ui-text-muted);
	line-height: 1.7;
}
.license-downloads {
	display: flex;
	flex-wrap: wrap;
	gap: 0.5rem 1.75rem;
	margin-top: 1rem;
	font-size: 0.875rem;
}
.license-downloads a {
	min-height: 2.75rem;
}
.license-filters {
	display: flex;
	gap: 0.75rem;
	margin-top: 2.5rem;
}
.category-select {
	width: 12rem;
}
.license-count,
.license-status {
	margin-block: 1.5rem;
	color: var(--ui-text-muted);
	font-size: 0.875rem;
}
.license-status button {
	text-decoration: underline;
}
.license-list {
	margin-bottom: 1.5rem;
}
.license-list li {
	display: grid;
	grid-template-columns: minmax(0, 1fr) minmax(12rem, 0.7fr);
	align-items: start;
	gap: 1rem;
	padding-block: 1rem;
}
.component-name {
	display: flex;
	flex-direction: column;
	align-items: flex-start;
	gap: 0.25rem;
	overflow-wrap: anywhere;
	font-weight: 500;
	color: var(--ui-text-highlighted);
}
.component-version {
	color: var(--ui-text-muted);
	font-size: 0.75rem;
	font-weight: 400;
}
.component-license {
	display: flex;
	flex-direction: column;
	align-items: flex-start;
	gap: 0.375rem;
	font-size: 0.875rem;
	overflow-wrap: anywhere;
}
.component-license a,
.missing-notice {
	font-size: 0.75rem;
	color: var(--ui-text-muted);
}
.licenses-content > footer {
	margin-top: 4rem;
	justify-content: flex-start;
}
@media (max-width: 600px) {
	.licenses-content {
		padding: 1rem 1.25rem 2rem;
	}
	.license-filters {
		flex-direction: column;
	}
	.category-select {
		width: 100%;
	}
	.license-list li {
		grid-template-columns: minmax(0, 1fr);
		gap: 0.5rem;
		padding-block: 1.25rem;
	}
	.component-license {
		flex-direction: row;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 1rem;
	}
}
</style>
