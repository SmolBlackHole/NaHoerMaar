<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";
const player = usePlayerStore();
const { icons } = useTheme();
</script>

<template>
	<div v-if="player.connection !== 'live'" class="player-notice bg-elevated/40" role="status">
		<p>
			{{
				player.connection === "connecting"
					? "Connecting to the bot…"
					: "Connection lost. Controls will return when the player is in sync."
			}}
		</p>
		<UButton
			v-if="player.connection === 'offline'"
			label="Reconnect"
			:icon="icons.reload"
			color="neutral"
			variant="outline"
			@click="player.connect"
		/>
	</div>
	<div v-if="player.error" class="player-notice" role="alert">
		<p class="flex-1">{{ player.error }}</p>
		<UButton
			v-if="player.uncertain"
			label="Check result"
			color="neutral"
			variant="outline"
			:loading="player.pending"
			:disabled="player.connection !== 'live'"
			@click="player.retry"
		/>
		<UButton
			v-else
			:icon="icons.close"
			aria-label="Dismiss message"
			color="neutral"
			variant="ghost"
			@click="player.error = null"
		/>
	</div>
	<div v-if="player.snapshot?.last_issue" class="player-notice" role="alert">
		{{
			player.snapshot.last_issue.fatal
				? "The bot stopped after an internal error. Restart the backend to continue."
				: player.snapshot.voice_state === "disconnected"
					? "The voice connection was lost. Rejoin a channel and press play to continue."
					: player.snapshot.state === "error"
						? "This track could not be played. Retry it or skip to the next one."
						: "A track could not be played and was skipped."
		}}
	</div>
</template>

<style scoped>
.player-notice {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: space-between;
	gap: 0.75rem;
	border-bottom: 1px solid var(--ui-border);
	padding: 0.75rem 1.25rem;
	font-size: 0.875rem;
}
@media (min-width: 640px) {
	.player-notice {
		padding-inline: 2rem;
	}
}
</style>
