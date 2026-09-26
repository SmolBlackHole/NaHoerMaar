<script setup lang="ts">
const core = useNuxtApp().$backendCore;
const player = core.stores.usePlayerStore();
const recent = core.workflows.recent();
const expanded = ref(false);
const history = computed(() => recent.recent.data.value ?? []);
const visible = computed(() => (expanded.value ? history.value : history.value.slice(0, 5)));

onMounted(() => void recent.load(50));
watch(
	() => player.state?.runtime.playback_id,
	(value, previous) => {
		if (previous && value !== previous) void recent.load(50);
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
				v-if="history.length > 5"
				:label="expanded ? 'Show less' : 'Show all'"
				:aria-expanded="expanded"
				aria-controls="history-tracks"
				variant="ghost"
				color="neutral"
				size="sm"
				class="min-h-11 shrink-0"
				@click="expanded = !expanded"
			/>
		</div>
		<div
			v-if="recent.recent.loading.value && !history.length"
			class="space-y-2"
			aria-busy="true"
		>
			<USkeleton v-for="row in 4" :key="row" class="h-16 w-full" />
		</div>
		<p
			v-else-if="recent.recent.error.value && !history.length"
			role="alert"
			class="text-sm text-warning"
		>
			{{ recent.recent.error.value }}
		</p>
		<PlayerRecentList v-else id="history-tracks" :entries="visible" />
	</section>
</template>

<style scoped>
.history-section {
	margin-top: 3rem;
	padding-top: 2rem;
	scroll-margin-top: 1rem;
}
@container workspace (max-width: 600px) {
	.history-section {
		margin-top: 2rem;
		padding-top: 0;
		border-top: 0;
	}
}
</style>
