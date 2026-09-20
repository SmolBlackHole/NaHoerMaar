<script setup lang="ts">
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from "reka-ui";
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
	{ label: "Player", value: "player", icon: icons.value.music },
	{ label: "Queue", value: "queue", icon: icons.value.list },
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
	<TabsRoot v-model="selected" :unmount-on-hide="false" class="player-tabs">
		<UDashboardPanel
			id="music-player"
			class="player-page min-w-0"
			:class="{ 'is-listening': selected === 'player' }"
			:ui="{ body: 'pt-0 sm:pt-0' }"
		>
			<template #header>
				<UDashboardNavbar class="player-navbar" :ui="{ left: 'h-full gap-3 sm:gap-4' }">
					<template #left>
						<UDashboardSidebarCollapse />
						<h1 class="sr-only">Music player</h1>
						<TabsList aria-label="Player views" class="music-tab-list">
							<TabsTrigger
								v-for="tab in tabs"
								:key="tab.value"
								:value="tab.value"
								class="music-tab-trigger"
							>
								<UIcon :name="tab.icon" class="size-5 shrink-0" />
								{{ tab.label }}
								<span v-if="tab.value === 'queue'" class="tab-count">{{
									player.snapshot?.upcoming.length ?? 0
								}}</span>
							</TabsTrigger>
						</TabsList>
					</template>
					<template #right>
						<span
							v-if="player.pending"
							role="status"
							class="sr-only text-muted text-xs sm:not-sr-only"
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
				<TabsContent value="player" class="music-tab-content">
					<PlayerNowPlaying :active="selected === 'player'" @queue="openQueue" />
				</TabsContent>
				<TabsContent value="queue" class="music-tab-content">
					<div class="queue-workspace">
						<PlayerQueue />
						<PlayerHistory />
					</div>
				</TabsContent>
			</template>
		</UDashboardPanel>
	</TabsRoot>
</template>

<style>
.player-tabs {
	display: flex;
	flex-direction: column;
	flex: 1;
	min-width: 0;
	min-height: 0;
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
.music-tab-list {
	display: flex;
	align-items: stretch;
	height: 100%;
	padding: 0;
	gap: 1.5rem;
}
.music-tab-trigger {
	position: relative;
	display: inline-flex;
	align-items: center;
	min-height: 2.75rem;
	padding-inline: 0.125rem;
	border-radius: 0;
	gap: 0.5rem;
	color: var(--ui-text-muted);
	font-size: 0.875rem;
	font-weight: 500;
	white-space: nowrap;
}
.music-tab-trigger:hover {
	color: var(--ui-text-highlighted);
}
.music-tab-trigger[data-state="active"] {
	color: var(--ui-text-highlighted);
}
.music-tab-trigger[data-state="active"]::after {
	content: "";
	position: absolute;
	inset-inline: 0;
	bottom: 0;
	height: 2px;
	background: var(--ui-primary);
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
.is-listening .music-tab-content[data-state="active"] {
	display: flex;
	flex: 1;
}
.is-listening .player-navbar {
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
	.music-tab-list {
		gap: 1rem;
	}
}
</style>
