<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";
const player = usePlayerStore();
const { icons } = useTheme();
const dismissed = ref(false);
const issue = computed(() => player.snapshot?.last_issue);
const dismissible = computed(
	() =>
		!issue.value?.fatal &&
		player.snapshot?.state !== "error" &&
		player.snapshot?.voice_state === "connected",
);
watch(
	() => `${issue.value?.entry_id}/${issue.value?.code}`,
	() => {
		dismissed.value = false;
	},
);
</script>

<template>
	<div v-if="player.connection !== 'live'" class="player-notice bg-elevated/40" role="status">
		<UIcon :name="icons.info" class="size-4 shrink-0" />
		<p class="min-w-0 flex-1">
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
	<div v-if="player.error" class="player-notice notice-warning" role="alert">
		<UIcon :name="icons.caution" class="size-4 shrink-0" />
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
	<div
		v-if="
			issue && player.connection === 'live' && !player.error && (!dismissed || !dismissible)
		"
		class="player-notice notice-warning"
		role="alert"
	>
		<UIcon :name="icons.caution" class="size-4 shrink-0" />
		<p class="min-w-0 flex-1">
			{{
				issue.fatal
					? "The bot stopped after an internal error. Restart the backend to continue."
					: player.snapshot?.voice_state === "disconnected"
						? "The voice connection was lost. Rejoin a channel and press play to continue."
						: player.snapshot?.state === "error"
							? "This track could not be played. Retry it or skip to the next one."
							: "A track could not be played and was skipped."
			}}
		</p>
		<UButton
			v-if="dismissible"
			:icon="icons.close"
			aria-label="Dismiss playback notice"
			color="neutral"
			variant="ghost"
			size="sm"
			@click="dismissed = true"
		/>
	</div>
</template>

<style scoped>
.player-notice {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: space-between;
	gap: 0.75rem;
	flex-shrink: 0;
	padding: 0.625rem 1.5rem;
	font-size: 0.75rem;
}
.notice-warning {
	background: color-mix(in srgb, var(--ui-warning) 10%, var(--ui-bg));
}
.notice-warning > :first-child {
	color: var(--ui-warning);
}
@media (min-width: 640px) {
	.player-notice {
		padding-inline: 2rem;
	}
}
</style>
