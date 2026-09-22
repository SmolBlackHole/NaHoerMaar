<script setup lang="ts">
const player = usePlayerStore();
const { icons } = useTheme();
const radio = computed(() => player.snapshot?.radio);
function control(action: "retry" | "stop") {
	if (radio.value?.generation)
		void (action === "stop" ? player.stopRadio : player.retryRadio)(radio.value.generation);
}
</script>

<template>
	<div v-if="radio && radio.state !== 'off'" class="radio-status" aria-label="Active radio">
		<UIcon :name="icons.radio" class="size-5 shrink-0 text-primary" />
		<div class="min-w-0 flex-1">
			<UTooltip :text="`Radio · ${radio.title}`">
				<p class="text-sm text-highlighted truncate">Radio · {{ radio.title }}</p>
			</UTooltip>
			<p class="mt-1 text-xs text-muted" role="status">
				{{
					radio.error ||
					(player.snapshot?.state === "paused"
						? "Refill paused"
						: radio.state === "loading"
							? "Finding the next tracks…"
							: `Started by ${radio.initiator?.name ?? "a listener"} · Your requests play first`)
				}}
			</p>
		</div>
		<div class="radio-actions">
			<UButton
				v-if="radio.state === 'waiting'"
				label="Try again"
				:icon="icons.reload"
				variant="ghost"
				color="neutral"
				class="min-h-11"
				:disabled="!player.enabled"
				:loading="player.isPending('radio.retried', radio.generation ?? undefined)"
				@click="control('retry')"
			/>
			<UButton
				label="End radio"
				variant="ghost"
				color="neutral"
				class="min-h-11"
				:disabled="!player.enabled"
				:loading="player.isPending('radio.stopped', radio.generation ?? undefined)"
				@click="control('stop')"
			/>
		</div>
	</div>
</template>

<style scoped>
.radio-status {
	display: flex;
	align-items: center;
	gap: 0.75rem;
	padding-block: 0.5rem 1.5rem;
}
.radio-actions {
	display: flex;
	flex-wrap: wrap;
}
@container workspace (max-width: 600px) {
	.radio-status {
		flex-wrap: wrap;
		align-items: start;
	}
	.radio-actions {
		margin-inline-start: auto;
	}
}
</style>
