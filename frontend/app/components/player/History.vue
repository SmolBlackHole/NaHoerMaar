<script setup lang="ts">
const core = useNuxtApp().$backendCore;
const player = core.stores.usePlayerStore();
const recent = core.workflows.recent();
const { icons } = useTheme();
const history = computed(() => recent.history.data.value?.entries ?? []);

onMounted(() => void recent.load({ pageSize: 10, newSnapshot: true }));
watch(
	() => player.state?.runtime.playback_id,
	(value, previous) => {
		if (previous && value !== previous) void recent.load({ pageSize: 10, newSnapshot: true });
	},
);
onScopeDispose(recent.dispose);
</script>

<template>
	<section id="recently-played" aria-labelledby="history-heading" class="history-section">
		<div class="mb-3 flex items-center justify-between gap-4">
			<h2 id="history-heading" class="text-xl font-semibold tracking-tight text-highlighted">
				Recently played
			</h2>
			<UButton
				to="/history"
				label="View all"
				variant="link"
				color="neutral"
				:trailing-icon="icons.arrowRight"
			/>
		</div>
		<div v-if="recent.history.loading.value && !history.length" aria-busy="true">
			<PlayerRecentList :entries="[]" loading />
		</div>
		<p
			v-else-if="recent.history.error.value && !history.length"
			role="alert"
			class="text-sm text-warning"
		>
			{{ recent.history.error.value }}
		</p>
		<div v-else id="history-tracks" class="history-scroll">
			<PlayerRecentList :entries="history" />
		</div>
	</section>
</template>

<style scoped>
.history-section {
	margin-top: 3rem;
	padding-top: 2rem;
	scroll-margin-top: 1rem;
}
.history-scroll {
	max-height: min(42rem, 60vh);
	overflow-y: auto;
	overscroll-behavior: contain;
	padding-inline-end: 0.5rem;
}
@container workspace (max-width: 600px) {
	.history-section {
		margin-top: 2rem;
		padding-top: 0;
		border-top: 0;
	}
}
</style>
