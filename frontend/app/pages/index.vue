<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";

useSeoMeta({ title: "Player | NaHörMaar" });
const player = usePlayerStore();
const { icons } = useTheme();
const route = useRoute();
const router = useRouter();
const selected = computed({
	get: () => (route.query.view === "queue" ? "queue" : "player"),
	set: (value: string | number) => {
		void router.replace({
			query: { ...route.query, view: value === "queue" ? "queue" : undefined },
		});
	},
});
const tabs = computed(() => [
	{ label: "Player", value: "player", slot: "player", icon: icons.value.music },
	{ label: "Queue", value: "queue", slot: "queue", icon: icons.value.list },
]);
async function openQueue() {
	await router.replace({ query: { ...route.query, view: "queue" } });
	await nextTick();
	requestAnimationFrame(() => {
		if (selected.value === "queue") document.getElementById("youtube-link")?.focus();
	});
}
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
	<UDashboardPanel
		id="music-player"
		class="player-page min-w-0"
		:class="{ 'is-listening': selected === 'player' }"
		:ui="{ body: 'pt-0 sm:pt-0' }"
	>
		<template #header>
			<UDashboardNavbar title="Player" class="player-navbar">
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
					<span class="connection-status" role="status"
						><span :class="{ 'is-live': player.connection === 'live' }" />{{
							connectionLabel
						}}</span
					>
				</template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<UTabs
				v-model="selected"
				:items="tabs"
				:unmount-on-hide="false"
				variant="link"
				class="music-tabs music-content"
				:ui="{
					list: 'music-tab-list',
					trigger: 'music-tab-trigger',
					indicator: 'transition-none',
					content: 'music-tab-content',
				}"
			>
				<template #trailing="{ item }"
					><span v-if="item.value === 'queue'" class="tab-count">{{
						player.snapshot?.upcoming.length ?? 0
					}}</span></template
				>
				<template #player
					><PlayerNowPlaying :active="selected === 'player'" @queue="openQueue"
				/></template>
				<template #queue>
					<div class="queue-workspace">
						<PlayerQueue />
						<PlayerHistory />
					</div>
				</template>
			</UTabs>
		</template>
	</UDashboardPanel>
</template>

<style>
.music-content {
	width: 100%;
}
.connection-status {
	display: inline-flex;
	align-items: center;
	gap: 0.5rem;
	font-size: 0.75rem;
	color: var(--ui-text-muted);
}
.connection-status > span {
	width: 0.375rem;
	height: 0.375rem;
	border-radius: 50%;
	background: var(--ui-text-dimmed);
}
.connection-status > .is-live {
	background: var(--ui-primary);
}
.music-tabs {
	align-items: stretch;
	gap: 1.5rem;
}
.music-tab-list {
	align-self: flex-start;
	padding: 0;
	gap: 1.5rem;
	border: 0;
}
.music-tab-trigger {
	flex: initial;
	padding: 0.75rem 0.125rem;
	border-radius: 0;
	gap: 0.5rem;
}
.music-tab-trigger[data-state="active"] {
	color: var(--ui-text-highlighted);
}
.tab-count {
	color: var(--ui-text-muted);
	font-size: 0.6875rem;
	font-variant-numeric: tabular-nums;
}
.music-tab-content {
	min-width: 0;
	outline-offset: 4px;
}
.player-page {
	isolation: isolate;
}
.player-page.is-listening {
	--ui-text: #e4e4e7;
	--ui-text-highlighted: #fafafa;
	--ui-text-muted: #b6b8c0;
	--ui-text-dimmed: #a1a1aa;
	--ui-primary: var(--color-primary-400);
	color: var(--ui-text);
}
.is-listening .music-tabs {
	flex: 1;
	max-width: none;
}
.is-listening .music-tab-content[data-state="active"] {
	display: flex;
	flex: 1;
}
.is-listening .player-navbar,
.is-listening .music-tab-list {
	position: relative;
	z-index: 2;
}
.queue-workspace {
	width: 100%;
}
.is-listening :focus-visible {
	outline-color: #fff;
}
@container workspace (max-width: 600px) {
	.music-tabs {
		gap: 1rem;
	}
}
</style>
