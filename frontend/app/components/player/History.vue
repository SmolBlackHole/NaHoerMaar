<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";
import { groupHistory } from "#shared/player";

const player = usePlayerStore();
const expanded = ref(false);
const history = computed(() => groupHistory(player.snapshot?.recently_played ?? []));
const visible = computed(() => (expanded.value ? history.value : history.value.slice(0, 5)));
</script>

<template>
	<section id="recently-played" aria-labelledby="history-heading" class="history-section">
		<div class="mb-3 flex items-center justify-between gap-4">
			<h2
				id="history-heading"
				class="text-xl font-semibold tracking-[-0.025em] text-highlighted"
			>
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
		<p v-if="!player.snapshot" role="status" class="text-sm text-muted">
			Waiting for playback history…
		</p>
		<PlayerRecentList v-else id="history-tracks" :entries="visible" />
	</section>
</template>

<style scoped>
.history-section {
	margin-top: 3rem;
	padding-top: 2rem;
	border-top: 1px solid var(--ui-border);
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
