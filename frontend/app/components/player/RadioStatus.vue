<script setup lang="ts">
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const { icons } = useTheme();
const radio = computed(() => player.state?.radio);
function control(action: "retry" | "stop") {
	void (action === "stop" ? player.stopRadio() : player.retryRadio());
}
</script>

<template>
	<div v-if="radio" class="radio-status" aria-label="Active radio">
		<UIcon :name="icons.radio" class="size-5 shrink-0 text-primary" />
		<div class="min-w-0 flex-1">
			<UTooltip text="Automatic queue">
				<p class="truncate text-sm text-highlighted">Radio</p>
			</UTooltip>
			<PlayerContributor
				v-if="radio.initiator"
				:contributor="radio.initiator"
				origin="radio"
				class="mt-1"
			/>
			<p class="mt-1 text-xs text-muted" role="status">
				{{
					radio.error ||
					(player.state?.runtime.phase === "paused"
						? "Refill paused"
						: radio.state === "loading"
							? "Finding the next tracks…"
							: "Keeps the queue filled · Your requests play first")
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
				:disabled="!player.canControl"
				:loading="player.isPending('radio.retry')"
				@click="control('retry')"
			/>
			<UButton
				label="End radio"
				variant="ghost"
				color="neutral"
				class="min-h-11"
				:disabled="!player.canControl"
				:loading="player.isPending('radio.stop')"
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
