<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";

defineProps<{ collapsed?: boolean }>();
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
const channelName = computed(
	() =>
		player.channels.find((channel) => channel.id === player.snapshot?.channel_id)?.name ??
		"Discord",
);
const status = computed(() => {
	if (player.connection !== "live")
		return player.connection === "connecting" ? "Connecting…" : "Offline";
	if (player.snapshot?.voice_state === "connecting") return "Joining…";
	return connected.value ? "Connected" : "Not connected";
});
</script>

<template>
	<UPopover :content="{ side: 'top', align: 'start', collisionPadding: 12 }">
		<button
			type="button"
			class="sidebar-voice"
			:class="{ 'is-collapsed': collapsed }"
			:aria-label="`Discord channel: ${channelName}. ${status}`"
			:title="collapsed ? `${channelName} · ${status}` : 'Manage Discord connection'"
		>
			<UIcon
				:name="icons.headphones"
				class="size-5 shrink-0"
				:class="connected && player.connection === 'live' ? 'text-primary' : 'text-muted'"
			/>
			<span v-if="!collapsed" class="min-w-0 flex-1 text-left">
				<span class="block truncate text-sm font-medium text-highlighted">{{
					channelName
				}}</span>
				<span role="status" class="mt-0.5 block text-xs text-muted">{{ status }}</span>
			</span>
			<UIcon v-if="!collapsed" :name="icons.chevronsUpDown" class="size-4 text-muted" />
		</button>
		<template #content>
			<section
				:aria-labelledby="`${id}-heading`"
				class="w-72 max-w-[calc(100vw-2rem)] space-y-3 p-4"
			>
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
						}}{{
							!channel.can_connect || !channel.can_speak
								? " (missing permissions)"
								: ""
						}}
					</option>
				</select>
				<p v-if="player.channelError" role="status" class="text-error text-sm">
					Couldn't load channels. Try refreshing.
				</p>
				<p
					v-else-if="
						player.connection === 'live' &&
						!player.channelsLoading &&
						!player.channels.length
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
						:disabled="
							!player.enabled || !available?.can_connect || !available?.can_speak
						"
						@click="
							player.mutate('/api/voice/channel', 'PUT', { channel_id: selected })
						"
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
			</section>
		</template>
	</UPopover>
</template>

<style scoped>
.sidebar-voice {
	display: flex;
	align-items: center;
	gap: 0.75rem;
	width: 100%;
	min-height: 3.5rem;
	padding: 0.5rem 0.625rem;
	border-radius: 0.5rem;
	cursor: pointer;
}
.sidebar-voice:hover,
.sidebar-voice[data-state="open"] {
	background: var(--ui-bg-elevated);
}
.sidebar-voice.is-collapsed {
	justify-content: center;
	min-height: 2.75rem;
	padding-inline: 0;
}
.channel-select {
	color: var(--ui-text);
}
</style>
