<script setup lang="ts">
const core = useNuxtApp().$backendCore;
const player = core.stores.usePlayerStore();
const recent = core.workflows.recent();
const loadingMore = ref(false);
const history = computed(() => recent.recent.data.value?.entries ?? []);
const hasMore = computed(() => Boolean(recent.recent.data.value?.next_cursor));

onMounted(() => void recent.load(20));
watch(
	() => player.state?.runtime.playback_id,
	(value, previous) => {
		if (previous && value !== previous) void recent.load(20);
	},
);
async function loadMore() {
	if (loadingMore.value || !hasMore.value) return;
	loadingMore.value = true;
	try {
		await recent.more(20);
	} finally {
		loadingMore.value = false;
	}
}
onScopeDispose(recent.dispose);
</script>

<template>
	<section id="recently-played" aria-labelledby="history-heading" class="history-section">
		<div class="mb-3 flex items-center justify-between gap-4">
			<h2 id="history-heading" class="text-xl font-semibold tracking-tight text-highlighted">
				Recently played
			</h2>
		</div>
		<div v-if="recent.recent.loading.value && !history.length" aria-busy="true">
			<PlayerRecentList :entries="[]" loading />
		</div>
		<p
			v-else-if="recent.recent.error.value && !history.length"
			role="alert"
			class="text-sm text-warning"
		>
			{{ recent.recent.error.value }}
		</p>
		<div v-else id="history-tracks" class="history-scroll">
			<PlayerRecentList :entries="history" />
			<div v-if="hasMore" class="flex justify-center py-3">
				<UButton
					label="Load more"
					:loading="loadingMore"
					:disabled="loadingMore"
					variant="ghost"
					color="neutral"
					class="min-h-11"
					@click="loadMore"
				/>
			</div>
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
