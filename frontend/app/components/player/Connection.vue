<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";
const player = usePlayerStore();
const { icons } = useTheme();
const visibility = useDocumentVisibility();
const mounted = useMounted();
</script>

<template>
	<div class="connection-controls">
		<UTooltip v-if="player.uncertain" text="Check the result of the last action">
			<UButton
				label="Check result"
				aria-label="Check the result of the last action"
				:icon="icons.caution"
				color="neutral"
				variant="ghost"
				class="recovery-button"
				:loading="player.pending"
				:aria-busy="player.pending"
				:disabled="player.pending || player.connection !== 'live'"
				@click="player.retry()"
			/>
		</UTooltip>
		<UTooltip
			v-if="player.connection !== 'live'"
			:text="
				player.connection === 'connecting'
					? 'Connecting to the bot'
					: 'Reconnect to the bot'
			"
		>
			<UButton
				label="Reconnect"
				:icon="icons.reload"
				color="neutral"
				variant="ghost"
				class="recovery-button"
				:loading="player.connection === 'connecting'"
				:aria-busy="player.connection === 'connecting'"
				:disabled="player.connection === 'connecting'"
				:aria-label="
					player.connection === 'connecting'
						? 'Connecting to the bot'
						: 'Reconnect to the bot'
				"
				@click="player.connect()"
			/>
		</UTooltip>
		<span
			v-else
			class="connection-status"
			:class="{ 'is-visible': mounted && visibility === 'visible' }"
			role="status"
			><span class="live-dot" />Live</span
		>
	</div>
</template>

<style scoped>
.connection-controls {
	display: flex;
	align-items: center;
	gap: 0.25rem;
}
.connection-status {
	display: inline-flex;
	align-items: center;
	gap: 0.5rem;
	font-size: 0.75rem;
	color: var(--ui-text-muted);
}
.live-dot {
	width: 0.375rem;
	height: 0.375rem;
	border-radius: 50%;
	background: var(--ui-primary);
}
.is-visible .live-dot {
	animation: live-breathe 3.2s ease-in-out infinite;
}
@keyframes live-breathe {
	0%,
	100% {
		opacity: 1;
	}
	50% {
		opacity: 0.45;
	}
}
.recovery-button {
	min-height: 2.75rem;
}
@media (max-width: 520px) {
	.recovery-button :deep([data-slot="label"]) {
		display: none;
	}
}
@media (prefers-reduced-motion: reduce) {
	.is-visible .live-dot {
		animation: none;
	}
}
</style>
