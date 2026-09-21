<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";

defineProps<{ collapsed?: boolean; compact?: boolean }>();
const player = usePlayerStore();
const { icons } = useTheme();
const id = useId();
const selected = ref("");
const selectedGuild = ref("");
const activeChannel = computed(() =>
	player.channels.find((channel) => channel.id === player.snapshot?.channel_id),
);
const guildOptions = computed(() =>
	Array.from(
		new Map(player.channels.map((channel) => [channel.guild_id, channel.guild_name])),
	).map(([value, label]) => ({ value, label })),
);
watch(
	[() => player.snapshot?.channel_id, () => player.channels],
	([channelId], previous) => {
		const active = activeChannel.value;
		if (active && (!selectedGuild.value || channelId !== previous?.[0])) {
			selectedGuild.value = active.guild_id;
			selected.value = active.id;
		}
		if (!guildOptions.value.some((guild) => guild.value === selectedGuild.value)) {
			selectedGuild.value =
				guildOptions.value.length === 1 ? guildOptions.value[0]!.value : "";
		}
		if (
			!player.channels.some(
				(channel) =>
					channel.id === selected.value && channel.guild_id === selectedGuild.value,
			)
		)
			selected.value = "";
	},
	{ immediate: true },
);
function chooseGuild(value: string) {
	selectedGuild.value = value;
	selected.value = activeChannel.value?.guild_id === value ? activeChannel.value.id : "";
}
const available = computed(() => player.channels.find((channel) => channel.id === selected.value));
const channelOptions = computed(() =>
	player.channels
		.filter((channel) => channel.guild_id === selectedGuild.value)
		.map((channel) => ({
			label: channel.name,
			value: channel.id,
			description:
				!channel.can_connect || !channel.can_speak ? "Missing permissions" : undefined,
			disabled: !channel.can_connect || !channel.can_speak,
		})),
);
const connected = computed(() => player.snapshot?.voice_state === "connected");
const switching = computed(() => connected.value && selected.value !== player.snapshot?.channel_id);
const channelName = computed(
	() =>
		player.channels.find((channel) => channel.id === player.snapshot?.channel_id)?.name ??
		"Discord",
);
const guildName = computed(
	() =>
		activeChannel.value?.guild_name ??
		guildOptions.value.find((guild) => guild.value === selectedGuild.value)?.label ??
		"Choose a server",
);
const status = computed(() => {
	if (player.connection !== "live")
		return player.connection === "connecting" ? "Connecting…" : "Offline";
	if (player.snapshot?.voice_state === "connecting") return "Joining…";
	return connected.value ? "Connected" : "Not connected";
});
</script>

<template>
	<UPopover
		:content="{
			side: compact ? 'bottom' : 'top',
			align: compact ? 'end' : 'start',
			collisionPadding: 12,
		}"
		@update:open="
			(open) => {
				if (open && player.connection === 'live') player.refreshChannels();
			}
		"
	>
		<button
			type="button"
			class="sidebar-voice"
			:class="{ 'is-collapsed': collapsed, 'is-compact': compact }"
			:aria-label="`${guildName}, channel: ${channelName}. ${status}`"
			:title="`${guildName} · ${channelName} · ${status}`"
		>
			<UIcon
				:name="icons.headphones"
				class="size-5 shrink-0"
				:class="connected && player.connection === 'live' ? 'text-primary' : 'text-muted'"
			/>
			<span v-if="compact" class="header-channel">{{
				connected ? channelName : "Connect"
			}}</span>
			<span v-else-if="!collapsed" class="min-w-0 flex-1 text-left">
				<span class="block truncate text-sm font-medium text-highlighted">{{
					channelName
				}}</span>
				<span class="mt-0.5 block truncate text-xs text-muted">{{ guildName }}</span>
				<span role="status" class="sr-only">{{ status }}</span>
			</span>
			<UIcon
				v-if="!collapsed && !compact"
				:name="icons.chevronsUpDown"
				class="size-4 text-muted"
			/>
		</button>
		<template #content>
			<section
				:aria-labelledby="`${id}-heading`"
				class="w-72 max-w-[calc(100vw-2rem)] space-y-3 p-4"
			>
				<div class="flex items-center justify-between gap-2">
					<h2 :id="`${id}-heading`" class="text-highlighted text-sm font-semibold">
						Discord
					</h2>
					<UButton
						:icon="icons.reload"
						aria-label="Refresh channels"
						color="neutral"
						variant="ghost"
						size="sm"
						:loading="player.channelsLoading"
						:aria-busy="player.channelsLoading"
						:disabled="player.connection !== 'live'"
						@click="player.refreshChannels()"
					/>
				</div>
				<p role="status" class="text-xs text-muted">{{ status }}</p>
				<label :for="`${id}-guild`" class="block text-xs text-muted">Server</label>
				<USelect
					:id="`${id}-guild`"
					:model-value="selectedGuild"
					:items="guildOptions"
					placeholder="Select a server"
					:trailing-icon="icons.chevronDown"
					variant="soft"
					class="min-h-11 w-full"
					:ui="{
						content: 'max-w-[calc(100vw-2rem)]',
						item: 'min-h-11 items-center',
						itemLabel: 'whitespace-normal',
					}"
					:disabled="!player.enabled || player.channelsLoading || !guildOptions.length"
					@update:model-value="chooseGuild"
				/>
				<label :for="`${id}-channel`" class="block text-xs text-muted">Voice channel</label>
				<USelect
					:id="`${id}-channel`"
					v-model="selected"
					:items="channelOptions"
					placeholder="Select a channel"
					:trailing-icon="icons.chevronDown"
					variant="soft"
					class="min-h-11 w-full"
					:ui="{
						content: 'max-w-[calc(100vw-2rem)]',
						item: 'min-h-11 items-center',
						itemLabel: 'whitespace-normal',
					}"
					:disabled="!player.enabled || player.channelsLoading || !selectedGuild"
				/>
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
					No voice channels available. Invite the bot to a server with a voice channel,
					then refresh.
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
						:loading="
							player.isPending('/api/connection', 'PUT') ||
							player.snapshot?.voice_state === 'connecting'
						"
						:aria-busy="
							player.isPending('/api/connection', 'PUT') ||
							player.snapshot?.voice_state === 'connecting'
						"
						:disabled="
							!player.enabled || !available?.can_connect || !available?.can_speak
						"
						@click="player.mutate('/api/connection', 'PUT', { channel_id: selected })"
					/>
					<UButton
						v-if="connected"
						label="Leave"
						:loading="player.isControlPending('leave')"
						:aria-busy="player.isControlPending('leave')"
						:icon="icons.logOut"
						color="neutral"
						variant="ghost"
						:disabled="!player.enabled"
						@click="player.mutate('/api/playback/control', 'POST', { action: 'leave' })"
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
	transition:
		background-color 140ms ease-out,
		color 140ms ease-out;
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
.sidebar-voice.is-compact {
	width: auto;
	min-height: 2.75rem;
	gap: 0.5rem;
	color: var(--ui-text-muted);
	font-size: 0.8125rem;
}
.header-channel {
	max-width: 10rem;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}
@media (max-width: 640px) {
	.header-channel {
		display: none;
	}
}
</style>
