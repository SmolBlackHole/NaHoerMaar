<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";

useSeoMeta({ title: "Player | NaHörMaar" });
const player = usePlayerStore();
const { icons } = useTheme();
const cinema = ref(false);
const channel = computed(() =>
	player.channels.find((item) => item.id === player.snapshot?.channel_id),
);
const connectionLabel = computed(() =>
	player.connection === "live"
		? "Live"
		: player.connection === "connecting"
			? "Connecting"
			: "Disconnected",
);
</script>

<template>
	<UDashboardPanel id="music-player" class="min-w-0">
		<template #header>
			<UDashboardNavbar title="Player">
				<template #leading><UDashboardSidebarCollapse /></template>
				<template #right>
					<span v-if="player.pending" role="status" class="text-muted text-xs"
						>Updating…</span
					>
					<span
						v-if="channel && player.snapshot?.voice_state === 'connected'"
						class="text-muted hidden items-center gap-2 text-sm sm:flex"
					>
						<UIcon :name="icons.headphones" class="size-4" /> {{ channel.name }}
					</span>
					<UBadge
						:label="connectionLabel"
						:color="player.connection === 'live' ? 'success' : 'neutral'"
						variant="subtle"
						role="status"
					/>
				</template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<div class="-m-4 min-h-full sm:-m-6">
				<PlayerNotice />
				<div
					class="music-workspace grid min-w-0 overflow-hidden"
					:class="!cinema && 'lg:grid-cols-[minmax(18rem,0.85fr)_minmax(0,1.35fr)]'"
				>
					<PlayerNowPlaying v-model:cinema="cinema" />
					<PlayerQueue />
				</div>
			</div>
		</template>
	</UDashboardPanel>
</template>
