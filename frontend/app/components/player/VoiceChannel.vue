<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";

const player = usePlayerStore();
const { icons } = useTheme();
const id = useId();
const selected = ref("");
watch(
	() => player.snapshot?.channel_id,
	(value) => {
		selected.value = value ?? "";
	},
	{ immediate: true },
);
const available = computed(() => player.channels.find((channel) => channel.id === selected.value));
const connected = computed(() => player.snapshot?.voice_state === "connected");
const switching = computed(() => connected.value && selected.value !== player.snapshot?.channel_id);
</script>

<template>
	<section :aria-labelledby="`${id}-heading`" class="space-y-3 px-2 py-5">
		<div class="flex items-center justify-between gap-2">
			<h2 :id="`${id}-heading`" class="text-highlighted text-sm font-semibold">
				Discord channel
			</h2>
			<UButton
				:icon="icons.reload"
				aria-label="Refresh channels"
				color="neutral"
				variant="ghost"
				size="sm"
				:loading="player.channelsLoading"
				:disabled="player.connection !== 'live'"
				@click="player.refreshChannels()"
			/>
		</div>
		<label :for="`${id}-channel`" class="sr-only">Voice channel</label>
		<select
			:id="`${id}-channel`"
			v-model="selected"
			class="channel-select w-full rounded-md border border-default bg-default p-2 text-sm"
			:disabled="!player.enabled || player.channelsLoading"
		>
			<option value="" disabled>Select a channel</option>
			<option
				v-for="channel in player.channels"
				:key="channel.id"
				:value="channel.id"
				:disabled="!channel.can_connect || !channel.can_speak"
			>
				{{ channel.name
				}}{{ !channel.can_connect || !channel.can_speak ? " (missing permissions)" : "" }}
			</option>
		</select>
		<p v-if="player.channelError" role="status" class="text-error text-sm">
			Couldn't load channels. Try refreshing.
		</p>
		<p
			v-else-if="
				player.connection === 'live' && !player.channelsLoading && !player.channels.length
			"
			class="text-muted text-sm"
		>
			No voice channels available.
		</p>
		<p v-if="switching" class="text-muted text-xs">
			Changing channels stops playback. The track stays in the queue.
		</p>
		<div class="flex flex-wrap gap-2">
			<UButton
				v-if="!connected || switching"
				:label="switching ? 'Move here' : 'Join channel'"
				:icon="icons.headphones"
				color="neutral"
				variant="outline"
				:loading="player.snapshot?.voice_state === 'connecting'"
				:disabled="!player.enabled || !available?.can_connect || !available?.can_speak"
				@click="player.mutate('/api/voice/channel', 'PUT', { channel_id: selected })"
			/>
			<UButton
				v-if="connected"
				label="Leave"
				:icon="icons.logOut"
				color="neutral"
				variant="ghost"
				:disabled="!player.enabled"
				@click="player.mutate('/api/voice/channel', 'DELETE')"
			/>
		</div>
		<p class="text-muted flex items-center gap-2 text-xs" role="status">
			<UIcon :name="icons.headphones" class="size-4" />
			{{
				player.snapshot?.voice_state === "connecting"
					? "Joining Discord…"
					: connected
						? `Connected to ${player.channels.find((channel) => channel.id === player.snapshot?.channel_id)?.name ?? "Discord"}`
						: "Not in a voice channel"
			}}
		</p>
	</section>
</template>
